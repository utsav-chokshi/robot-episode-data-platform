import json
import pytest
pytest.importorskip('pyarrow')
pytest.importorskip('duckdb')
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text
from robotics_data_platform.planning.ingestion import ProposalReceiver
from robotics_data_platform.analytics.snapshot import freeze_inputs
from robotics_data_platform.analytics.files import load_input
from robotics_data_platform.analytics.build import publish,rebuild
from proposal_helpers import make_attempt


def test_inference_tables_denominators_corruption_and_frozen_rebuild(fresh_database,tmp_path,monkeypatch):
    command.upgrade(Config('alembic.ini'),'head')
    engine=create_engine(fresh_database)
    receiver=ProposalReceiver(engine,tmp_path/'raw')
    first,_=make_attempt(engine,tmp_path)
    second,_=make_attempt(engine,tmp_path,'MODEL_TIMEOUT',None)
    receiver.ingest(tmp_path/'proposals',first['proposal_id'])
    receiver.ingest(tmp_path/'proposals',second['proposal_id'])
    definition={'cases':[{'case_id':'unattempted','request':{'object_id':'package_01'},'expected_outcome':'SUCCEEDED'}]}
    identity=freeze_inputs(engine,receiver.store.root,tmp_path/'datasets',definition,{'results':[]})
    snapshot=load_input(tmp_path/'datasets',identity)
    monkeypatch.setattr('robotics_data_platform.planning.adapter.SmolProvider.infer',lambda *args:pytest.fail('Dataset build must not infer'))
    result=publish(engine,receiver.store.root,tmp_path/'datasets',identity)
    assert len(result['tables'])==12 and result['tables']['proposals']['rows']==2
    assert result['tables']['proposal_validations']['rows']==1
    assert result['metrics']['proposals']['schema_valid_all_attempts']=={'numerator':0,'denominator':2,'rate':0.0}
    assert result['metrics']['proposals']['schema_valid_completed']['denominator']==1
    assert result['metrics']['coverage']['denominator']==1 and result['metrics']['coverage']['numerator']==0
    assert publish(engine,receiver.store.root,tmp_path/'datasets',identity)==result
    with engine.begin() as connection:
        connection.execute(text("UPDATE plan_attempts SET provider_status='MODEL_ERROR'"))
    assert rebuild(receiver.store.root,tmp_path/'datasets',result['build_fingerprint'])['metrics_match']
    changed=freeze_inputs(engine,receiver.store.root,tmp_path/'datasets',definition,{'results':[]})
    assert all(not m['eligible'] for m in load_input(tmp_path/'datasets',changed)['proposals']['members'])
    artifact=next(a for a in first['artifacts'] if a['role']=='model_response')
    receiver.store.object_path(artifact['sha256']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):
        rebuild(receiver.store.root,tmp_path/'datasets',result['build_fingerprint'])
    engine.dispose()
