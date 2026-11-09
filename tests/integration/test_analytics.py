"""Publication and denominator tests; synthetic fixtures are not robot benchmarks."""
import json
from pathlib import Path
import pytest
pytest.importorskip('pyarrow')
pytest.importorskip('duckdb')
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text
from robotics_data_platform.analytics.snapshot import freeze_inputs,expected_slots
from robotics_data_platform.analytics.files import load_input,verify_published
from robotics_data_platform.analytics.build import publish,rebuild
from robotics_data_platform.analytics.transform import materialize
from robotics_data_platform.ingestion.worker import Receiver
from robotics_data_platform.ingestion.repository import counts
from ingestion_helpers import success_bundle

pytestmark = pytest.mark.integration


@pytest.fixture
def setup(fresh_database,tmp_path):
    command.upgrade(Config('alembic.ini'),'head')
    engine = create_engine(fresh_database)
    receiver = Receiver(engine,tmp_path/'raw')
    source = success_bundle(tmp_path/'capture')
    receiver.ingest(source)
    definition = {'cases':[{'case_id':name,'request':{'object_id':'package_01','destination_id':'zone_a','seed':7},'expected_outcome':'SUCCEEDED'} for name in ['present','missing']]}
    assignment = {'results':[{**definition['cases'][0],'actual':{'episode_id':source.name}}]}
    root = tmp_path/'datasets'
    identity = freeze_inputs(engine,receiver.store.root,root,definition,assignment)
    yield engine,receiver,root,identity,definition,assignment
    engine.dispose()


def test_publish_repeat_rebuild_missing_slot_and_live_catalog_independence(setup,tmp_path):
    engine,receiver,root,identity,definition,assignment = setup
    before = counts(engine)
    assert freeze_inputs(engine,receiver.store.root,root,definition,assignment) == identity
    first = publish(engine,receiver.store.root,root,identity)
    assert first['metrics']['coverage'] == {'numerator':1,'denominator':2,'rate':.5}
    assert first['metrics']['physical_task_success']['denominator'] == 2
    assert publish(engine,receiver.store.root,root,identity) == first
    with engine.begin() as connection:
        connection.execute(text("UPDATE episodes SET outcome='FAILED'"))
    assert rebuild(receiver.store.root,root,first['build_fingerprint'])['metrics_match']
    assert counts(engine) == before
    next_identity = freeze_inputs(engine,receiver.store.root,root,definition,assignment)
    assert next_identity != identity
    snapshot = load_input(root,next_identity)
    assert snapshot['members'][0]['seal']['quality'] == 'CORRUPT'
    tables,metrics = materialize(snapshot,tmp_path/'excluded')
    assert tables['episodes']['rows'] == 1 and tables['events']['rows'] == 0
    assert metrics['coverage']['numerator'] == 0


@pytest.mark.parametrize('boundary',['staged','promoted'])
def test_crash_recovery(setup,boundary):
    engine,receiver,root,identity,_,_ = setup
    def fail(stage):
        if stage == boundary:
            raise RuntimeError('injected publication crash')
    with pytest.raises(RuntimeError,match='injected'):
        publish(engine,receiver.store.root,root,identity,fail)
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM dataset_versions')) == 0
    result = publish(engine,receiver.store.root,root,identity)
    assert verify_published(root,result['build_fingerprint']) == result
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM dataset_versions')) == 1


def test_raw_corruption_blocks_publish_and_records_exclusion_on_new_freeze(setup):
    engine,receiver,root,identity,definition,assignment = setup
    member = load_input(root,identity)['members'][0]
    path = receiver.store.object_path(member['bundle']['artifacts'][0]['sha256'])
    path.write_bytes(b'corrupted')
    with pytest.raises(Exception,match='checksum'):
        publish(engine,receiver.store.root,root,identity)
    assert not (root/'versions').exists()
    new_identity = freeze_inputs(engine,receiver.store.root,root,definition,assignment)
    assert not load_input(root,new_identity)['members'][0]['seal']['eligible']


def test_published_tamper_never_overwritten(setup):
    engine,receiver,root,identity,_,_ = setup
    result = publish(engine,receiver.store.root,root,identity)
    from fastapi.testclient import TestClient
    from robotics_data_platform.api.factory import create_app
    from robotics_data_platform.settings import Settings
    settings = Settings(database_url=engine.url.render_as_string(hide_password=False),raw_root=receiver.store.root,dataset_root=root)
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v1/datasets/'+result['dataset_id']).status_code == 200
        assert client.get('/api/v1/datasets').json()['items'][0]['dataset_id'] == result['dataset_id']
    path = root/'versions'/result['build_fingerprint']/'events.parquet'
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='checksum'):
        publish(engine,receiver.store.root,root,identity)
    assert path.read_bytes() == b'tampered'
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v1/datasets/'+result['dataset_id']).status_code == 503


def test_assignment_labels_and_duplicate_attempt_rejected(setup):
    _,_,_,_,definition,assignment = setup
    assignment['results'].append({**definition['cases'][1],'actual':assignment['results'][0]['actual']})
    with pytest.raises(ValueError,match='two expected slots'):
        expected_slots(definition,assignment)
    assignment['results'][0]['expected_outcome'] = 'FAILED'
    with pytest.raises(ValueError,match='labels differ'):
        expected_slots(definition,assignment)


def test_failed_and_rejected_are_retained(setup,tmp_path):
    _,_,root,identity,_,_ = setup
    snapshot = load_input(root,identity)
    member = snapshot['members'][0]
    # Transform-only synthetic outcomes; not source validation or robot measurements.
    for outcome,category in [('FAILED','injected_fault'),('REJECTED','rejection')]:
        member['catalog']['outcome'] = outcome
        member['bundle']['summary']['outcome'] = outcome
        snapshot['task_set']['slots'] = [{**snapshot['task_set']['slots'][0],'expected_outcome':outcome,'category':category}]
        tables,metrics = materialize(snapshot,tmp_path/outcome)
        assert tables['episodes']['rows'] == 1
        assert metrics['expectation_match']['numerator'] == 1
        assert metrics['physical_task_success']['numerator'] == 0
