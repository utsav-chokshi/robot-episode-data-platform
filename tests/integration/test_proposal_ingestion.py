import json
import shutil
from datetime import datetime,timedelta
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from robotics_data_platform.planning.ingestion import ProposalReceiver
from robotics_data_platform.ingestion.worker import Receiver
from robotics_data_platform.ingestion.repository import counts
from robotics_data_platform.ingestion.storage import RawStore
from robotics_data_platform.api.factory import create_app
from robotics_data_platform.settings import Settings
from proposal_helpers import make_attempt,rewrite

pytestmark=pytest.mark.integration


@pytest.fixture
def setup(fresh_database,tmp_path):
    command.upgrade(Config('alembic.ini'),'head')
    engine=create_engine(fresh_database)
    receiver=ProposalReceiver(engine,tmp_path/'raw')
    yield engine,receiver,tmp_path
    engine.dispose()


def total(engine,table):
    with engine.connect() as connection:
        return connection.scalar(text('SELECT count(*) FROM '+table))


@pytest.mark.parametrize('status,response,schema',[('COMPLETED',b'malformed response',False),('COMPLETED',b'{"status":"ready","object_id":"package_01","destination_id":"zone_a"}',True),('MODEL_TIMEOUT',None,None),('MODEL_UNAVAILABLE',None,None)])
def test_attempts_ingest_duplicate_and_replay_without_model_or_spool(setup,monkeypatch,status,response,schema):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root,status,response)
    baseline=counts(engine)
    monkeypatch.setattr('robotics_data_platform.planning.adapter.SmolProvider.infer',lambda *args:pytest.fail('Data replay cannot infer'))
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='INGESTED'
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='DUPLICATE'
    shutil.move(str(root/'proposals'),str(root/'unavailable-spool'))
    assert receiver.replay()[0]['status']=='DUPLICATE'
    assert total(engine,'plan_attempts')==1 and counts(engine)==baseline
    assert total(engine,'validation_results')==(0 if schema is None else 1)
    with TestClient(create_app(Settings(database_url=engine.url.render_as_string(hide_password=False),raw_root=root/'raw'))) as client:
        value=client.get('/api/v1/proposals/'+manifest['proposal_id']).json()
        assert value['provider_status']==status and value['execution_eligible'] is False
        assert (value['validation']['schema_valid'] if value['validation'] else None)==schema
        image=next(a for a in value['artifacts'] if a['role']=='proposal_image')
        assert client.get('/api/v1/proposal-artifacts/'+image['artifact_id']).content.startswith(b'\x89PNG')


@pytest.mark.parametrize('boundary',['raw_persisted','db_committed'])
def test_receiver_crash_boundaries_recover_from_raw_receipts(setup,boundary):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root)
    def fail(stage):
        if stage==boundary:
            raise RuntimeError('injected crash')
    receiver.checkpoint=fail
    with pytest.raises(RuntimeError):
        receiver.ingest(root/'proposals',manifest['proposal_id'])
    receiver.checkpoint=lambda stage:None
    assert receiver.replay()[0]['status']==('INGESTED' if boundary=='raw_persisted' else 'DUPLICATE')
    assert total(engine,'plan_attempts')==1


def test_database_outage_keeps_durable_receipt(setup):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root)
    class Down:
        def begin(self):
            raise SQLAlchemyError('database unavailable')
    receiver.engine=Down()
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='PENDING_DB'
    assert total(engine,'plan_attempts')==0
    receiver.engine=engine
    assert receiver.replay()[0]['status']=='INGESTED'


def test_source_projection_pending_then_resolves(setup):
    engine,receiver,root=setup
    manifest,source=make_attempt(engine,root,project_source=False)
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='PENDING_REFERENCES'
    Receiver(engine,root/'raw').ingest(source)
    assert receiver.replay()[0]['status']=='INGESTED'
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM proposal_findings WHERE resolved_by_receipt IS NULL'))==0


def test_missing_artifact_and_partial_capture_are_audited_then_recover(setup):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root)
    directory=root/'proposals'/'attempts'/manifest['proposal_id']
    ready=(directory/'ready.json').read_bytes()
    (directory/'ready.json').unlink()
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='PENDING_REFERENCES'
    (directory/'ready.json').write_bytes(ready)
    response=next(a for a in manifest['artifacts'] if a['role']=='model_response')
    path=RawStore(root/'proposals').object_path(response['sha256'])
    data=path.read_bytes()
    path.unlink()
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='PENDING_REFERENCES'
    path.write_bytes(data)
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='INGESTED'
    assert total(engine,'proposal_receipts')==3 and total(engine,'plan_attempts')==1


@pytest.mark.parametrize('change',['validation','execution','cross_episode'])
def test_forged_semantics_or_cross_episode_bindings_quarantine(setup,change):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root)
    if change=='validation':
        manifest['validation']['schema_valid']=True
    elif change=='execution':
        manifest['execution_eligible']=True
    else:
        manifest['artifacts'][0]['episode_id']=str(uuid4())
    rewrite(root,manifest)
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='QUARANTINED'
    assert total(engine,'plan_attempts')==0 and total(engine,'proposal_artifacts')==0


def test_changed_final_identity_conflicts_and_keeps_original(setup):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root)
    first=receiver.ingest(root/'proposals',manifest['proposal_id'])
    manifest['finished_at_utc']=(datetime.fromisoformat(manifest['finished_at_utc'])+timedelta(seconds=1)).isoformat()
    rewrite(root,manifest)
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='CONFLICTED'
    assert total(engine,'plan_attempts')==1
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT receipt_sha256 FROM plan_attempts'))==first['receipt_sha256']


def test_database_rejects_execution_flag_and_wrong_source_episode(setup):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root)
    receiver.ingest(root/'proposals',manifest['proposal_id'])
    for sql in ['UPDATE plan_attempts SET execution_eligible=true','UPDATE plan_attempts SET source_episode_id=gen_random_uuid()']:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(sql))


def test_corrupt_bytes_remain_quarantined_until_original_restored(setup):
    engine,receiver,root=setup
    manifest,_=make_attempt(engine,root)
    item=next(a for a in manifest['artifacts'] if a['role']=='model_response')
    path=RawStore(root/'proposals').object_path(item['sha256'])
    original=path.read_bytes()
    path.write_bytes(b'bad')
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='QUARANTINED'
    assert total(engine,'plan_attempts')==0
    path.write_bytes(original)
    assert receiver.ingest(root/'proposals',manifest['proposal_id'])['status']=='INGESTED'


def test_artifact_identity_conflict_rolls_back_all_new_projection_rows(setup):
    engine,receiver,root=setup
    first,_=make_attempt(engine,root)
    receiver.ingest(root/'proposals',first['proposal_id'])
    second,_=make_attempt(engine,root)
    second['artifacts'][0]['artifact_id']=first['artifacts'][0]['artifact_id']
    rewrite(root,second)
    before=total(engine,'proposal_artifacts')
    assert receiver.ingest(root/'proposals',second['proposal_id'])['status']=='CONFLICTED'
    assert total(engine,'plan_attempts')==1 and total(engine,'proposal_artifacts')==before
