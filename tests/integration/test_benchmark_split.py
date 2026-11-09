import pytest
pytest.importorskip('pyarrow')
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from robotics_data_platform.analytics.snapshot import freeze_inputs
from robotics_data_platform.analytics.files import load_input
from robotics_data_platform.analytics.transform import materialize
from robotics_data_platform.ingestion.worker import Receiver
from ingestion_helpers import success_bundle


def test_fixed_cohort_metadata_does_not_relabel_historical_development(fresh_database,tmp_path):
    command.upgrade(Config('alembic.ini'),'head')
    engine=create_engine(fresh_database)
    source=success_bundle(tmp_path/'capture')
    Receiver(engine,tmp_path/'raw').ingest(source)
    case={'case_id':'one','request':{'object_id':'package_01','destination_id':'zone_a','seed':7},'expected_outcome':'SUCCEEDED'}
    definition={'cases':[case]}
    assignment={'results':[{**case,'actual':{'episode_id':source.name}}]}
    old=freeze_inputs(engine,tmp_path/'raw',tmp_path/'datasets',definition,assignment)
    fixed=freeze_inputs(engine,tmp_path/'raw',tmp_path/'datasets',definition,assignment,task_set_id='test-fixed',split='fixed_evaluation')
    assert old!=fixed
    _,old_metrics=materialize(load_input(tmp_path/'datasets',old),tmp_path/'old')
    _,fixed_metrics=materialize(load_input(tmp_path/'datasets',fixed),tmp_path/'fixed')
    assert old_metrics['split']=='development' and fixed_metrics['split']=='fixed_evaluation'
    assert old_metrics['physical_task_success']==fixed_metrics['physical_task_success']
    assert load_input(tmp_path/'datasets',old)['task_set']['slots'][0]['split']=='development'
    assert load_input(tmp_path/'datasets',fixed)['task_set']['slots'][0]['split']=='fixed_evaluation'
    engine.dispose()
