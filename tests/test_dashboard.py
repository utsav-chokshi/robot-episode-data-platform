"""UI acceptance checks using Streamlit's simulated app runtime."""
import io
from pathlib import Path
import pytest
st=pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
from PIL import Image
from robotics_data_platform.dashboard import client

DATASET='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
ROBOT='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
MODEL='cccccccc-cccc-4ccc-8ccc-cccccccccccc'
TIMEOUT='dddddddd-dddd-4ddd-8ddd-dddddddddddd'


@pytest.fixture
def app(monkeypatch):
    stream=io.BytesIO()
    Image.new('RGB',(8,8),(0,120,100)).save(stream,format='PNG')
    png=stream.getvalue()
    ratio=lambda n,d:{'numerator':n,'denominator':d,'rate':n/d if d else None}
    manifest={'dataset_id':DATASET,'build_fingerprint':'1'*64,'metrics':{'catalog_episodes':17,'physical_task_success':ratio(13,14),'normal_task_success':ratio(13,13),'rejection_match':ratio(3,3),'quality_violations':{'unique_episodes':0},'proposals':{'attempts':3,'schema_valid_completed':ratio(0,2),'provider_completion':ratio(2,3),'execution_eligible':ratio(0,3),'quality_violations':{'no_execution_eligibility':0}}},'tables':{'episodes':{'rows':17}}}
    report={'report_id':'planned','dataset_id':DATASET,'split':'planned_evaluation','task_set_sha256':'2'*64,'assignment_sha256':'3'*64,'fingerprint':'4'*64,'result':{'task_set':{'disclosure':'Frozen planned benchmark, not executed.'},'metrics':{},'rows':[{'case_id':'planned-red','mode':'VLM_EVALUATION','failure_code':'MISSING_ATTEMPT'}]}}
    for mode,total,na in [('BASELINE_STRUCTURED',54,36),('VLM_EVALUATION',90,0)]:
        report['result']['metrics'][mode]={'coverage':ratio(0,total),'complete_evidence':ratio(0,total),'expectation_match':ratio(0,total),'physical_success':ratio(0,total) if mode=='BASELINE_STRUCTURED' else {'status':'NOT_APPLICABLE'},'schema_valid':ratio(0,total),'not_applicable':na}
    def read(path,binary=False):
        if binary:
            if 'instruction' in path:return b'Move the red package to zone A.'
            if 'response' in path:return b' zone_a.move_package_to_zone_a'
            return png
        if path=='/api/v1/datasets':return {'items':[{'dataset_id':DATASET,'created_at_utc':'2026-11-09T00:00:00Z'}]}
        if path=='/api/v1/datasets/'+DATASET:return manifest
        if path=='/api/v1/evaluations':return {'items':[{'report_id':'planned','dataset_id':DATASET,'task_set_name':'tabletop-evaluation-v1','split':'planned_evaluation'}]}
        if path=='/api/v1/evaluations/planned':return report
        if path.startswith('/api/v1/episodes?'):return {'items':[{'episode_id':ROBOT,'outcome':'SUCCEEDED'}]}
        if path=='/api/v1/episodes/'+ROBOT:return {'outcome':'SUCCEEDED','completeness_status':'COMPLETE','failure_code':None,'actions':[{'status':'SUCCEEDED','duration_ms':100}],'artifacts':[{'artifact_id':'image','role':'initial'}],'source_summary':{}}
        if '/events?' in path:return {'items':[{'event_type':'observation.captured','producer_sequence':i,'simulation_time_ns':i*1000000000,'captured_at_utc':'2026-11-09T00:00:00Z','payload':{'image_artifact_id':'image'+str(i)}} for i in [1,2]]}
        if path.startswith('/api/v1/proposals?'):return {'items':[{'proposal_id':MODEL,'provider_status':'COMPLETED'},{'proposal_id':TIMEOUT,'provider_status':'MODEL_TIMEOUT'}]}
        if path.startswith('/api/v1/proposals/'):
            timeout=path.endswith(TIMEOUT)
            artifacts=[{'role':'proposal_image','artifact_id':'image'},{'role':'instruction','artifact_id':'instruction'}]+([] if timeout else [{'role':'model_response','artifact_id':'response'}])
            return {'provider_status':'MODEL_TIMEOUT' if timeout else 'COMPLETED','source_episode_id':ROBOT,'artifacts':artifacts,'validation':None if timeout else {'schema_valid':False},'manifest':{'provider_metadata':{'wall_ms':50 if timeout else 18000}},'model_id':'test-provider','model_revision':'a'*40,'configuration':{}}
        return {'items':[]}
    monkeypatch.setattr(client,'read',read)
    st.cache_data.clear()
    return AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/robotics_data_platform/dashboard/app.py'),default_timeout=20)


@pytest.mark.parametrize('page',['Overview','Evaluations','Episodes','Proposals','Data quality'])
def test_every_page_renders_without_running_models_or_robots(app,page):
    app.run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
    assert not any(button.label.lower().startswith(('run model','execute','start robot')) for button in app.button)
    if page=='Evaluations':
        assert any('NOT RUN' in warning.value for warning in app.warning)
        assert any(metric.value=='0 / 90' for metric in app.metric)
    if page=='Proposals':
        assert any('zone_a.move_package_to_zone_a' in code.value for code in app.code)
        assert any('Invalid proposal schema' in error.value for error in app.error)


def test_timeout_has_no_fictional_response_or_invalid_schema_label(app):
    app.run()
    app.sidebar.radio[0].set_value('Proposals').run()
    selector=next(box for box in app.selectbox if box.label=='Model attempt')
    selector.set_value(TIMEOUT).run()
    assert not app.exception
    assert any('No completed response' in info.value for info in app.info)
    assert not any('Invalid proposal schema' in error.value for error in app.error)


def test_api_outage_is_a_readable_message(app,monkeypatch):
    def unavailable(*args,**kwargs):
        raise client.APIError('Evidence service temporarily unavailable')
    monkeypatch.setattr(client,'read',unavailable)
    st.cache_data.clear()
    app.run()
    assert not app.exception and any('temporarily unavailable' in error.value for error in app.error)
