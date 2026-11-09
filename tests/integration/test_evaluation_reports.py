import pytest
pytest.importorskip('pyarrow')
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text
from sqlalchemy.exc import SQLAlchemyError
from fastapi.testclient import TestClient
from robotics_data_platform.analytics.snapshot import freeze_inputs
from robotics_data_platform.analytics.build import publish
from robotics_data_platform.evaluation.contracts import TaskSet,Assignments
from robotics_data_platform.evaluation.reports import publish_report
from robotics_data_platform.api.factory import create_app
from robotics_data_platform.settings import Settings
from proposal_helpers import make_attempt


def test_report_recovery_reuse_and_api_tamper_detection(fresh_database,tmp_path,monkeypatch):
    command.upgrade(Config('alembic.ini'),'head')
    engine=create_engine(fresh_database)
    make_attempt(engine,tmp_path)
    definition={'cases':[{'case_id':'missing','request':{'object_id':'package_01'},'expected_outcome':'SUCCEEDED'}]}
    identity=freeze_inputs(engine,tmp_path/'raw',tmp_path/'datasets',definition,{'results':[]})
    dataset=publish(engine,tmp_path/'raw',tmp_path/'datasets',identity)
    task=TaskSet.model_validate({'name':'test-v1','split':'planned_evaluation','disclosure':'Synthetic report recovery check.','cases':[{'case_id':'missing','category':'supported','instruction':'Move red.','expected':{'status':'ready','object_id':'package_01','destination_id':'zone_a'},'baseline_request':{'object_id':'package_01','destination_id':'zone_a'},'baseline_outcome':'SUCCEEDED','seeds':[7]}]})
    from robotics_data_platform.evaluation import reports
    original=reports.text
    def fail_registration(sql):
        if sql.startswith('INSERT INTO evaluation_runs'):
            raise SQLAlchemyError('injected after promotion')
        return original(sql)
    monkeypatch.setattr(reports,'text',fail_registration)
    with pytest.raises(SQLAlchemyError):
        publish_report(engine,tmp_path/'datasets',tmp_path/'evaluations',dataset['dataset_id'],task,Assignments(bindings=[]))
    monkeypatch.setattr(reports,'text',original)
    result=publish_report(engine,tmp_path/'datasets',tmp_path/'evaluations',dataset['dataset_id'],task,Assignments(bindings=[]))
    assert publish_report(engine,tmp_path/'datasets',tmp_path/'evaluations',dataset['dataset_id'],task,Assignments(bindings=[]))==result
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM evaluation_runs'))==1
    settings=Settings(database_url=fresh_database,raw_root=tmp_path/'raw',dataset_root=tmp_path/'datasets',evaluation_root=tmp_path/'evaluations')
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v1/evaluations/'+result['report_id']).status_code==200
        path=tmp_path/'evaluations'/'reports'/result['fingerprint']/'result.json'
        path.write_bytes(b'changed')
        assert client.get('/api/v1/evaluations/'+result['report_id']).status_code==503
    with pytest.raises(ValueError,match='checksum'):
        publish_report(engine,tmp_path/'datasets',tmp_path/'evaluations',dataset['dataset_id'],task,Assignments(bindings=[]))
    engine.dispose()
