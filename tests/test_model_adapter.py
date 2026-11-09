import hashlib
import json
import sys
import time
from pathlib import Path
from uuid import uuid4
import pytest
from robotics_data_platform.planning.adapter import PROFILE,ProviderResult,run_child
from robotics_data_platform.planning.attempts import Vocabulary,capture_attempt,verify_attempt
from robotics_data_platform.planning.model_cache import verified_snapshot


class StubProvider:
    profile = {**PROFILE,'provider':'test-stub-not-inference'}
    def __init__(self,status='COMPLETED',response=b'{"status":"ready","object_id":"package_01","destination_id":"zone_a"}'):
        self.status,self.response = status,response
    def infer(self,request,working,timeout_seconds):
        # Assert that evaluator state is not included in the provider request.
        value = json.loads(request.read_bytes())
        assert set(value) == {'image_path','prompt','profile'}
        assert 'expected_outcome' not in value['prompt'] and 'object_xyz' not in value['prompt']
        return ProviderResult(self.status,self.response,{'timings_ms':{'generation':1}})


@pytest.fixture
def inputs(tmp_path):
    image = b'synthetic-bytes-for-parent-evidence-test-not-an-inference-image'
    source = {'episode_id':str(uuid4()),'observation_id':str(uuid4()),'image_artifact_id':str(uuid4()),'image_sha256':hashlib.sha256(image).hexdigest()}
    vocabulary = Vocabulary.model_validate({'version':'test-v1','objects':[{'id':'package_01','description':'red package'}],'destinations':[{'id':'zone_a','description':'zone A'}]})
    return tmp_path,image,source,'Move the red package to zone A',vocabulary


def test_retries_are_distinct_attempts_and_preserve_original_response(inputs):
    root,*arguments = inputs
    first = capture_attempt(root,*arguments,StubProvider())
    second = capture_attempt(root,*arguments,StubProvider())
    assert first['proposal_id'] != second['proposal_id'] and first['episode_id'] != second['episode_id']
    assert first['source'] == second['source']
    assert not first['execution_eligible'] and first['outcome'] == 'NOT_EXECUTED'
    assert verify_attempt(root,first['proposal_id']) == first
    item = next(a for a in first['artifacts'] if a['role']=='model_response')
    assert item['sha256'] == hashlib.sha256(StubProvider().response).hexdigest()


@pytest.mark.parametrize('status',['MODEL_TIMEOUT','MODEL_UNAVAILABLE','MODEL_ERROR'])
def test_provider_failures_have_no_fictional_response_or_validation(inputs,status):
    root,*arguments = inputs
    result = capture_attempt(root,*arguments,StubProvider(status,None))
    assert result['provider_status'] == status and result['validation'] is None
    assert not any(a['role']=='model_response' for a in result['artifacts'])
    assert verify_attempt(root,result['proposal_id']) == result


def test_bad_model_format_is_completed_inference_but_invalid_proposal(inputs):
    root,*arguments = inputs
    result = capture_attempt(root,*arguments,StubProvider(response=b'Here is a plan: run_robot()'))
    assert result['provider_status'] == 'COMPLETED'
    assert not result['validation']['schema_valid']
    assert not result['execution_eligible']


@pytest.mark.parametrize('boundary',['inputs_persisted','provider_returned','manifest_persisted'])
def test_crash_keeps_visible_partial_evidence_not_a_ready_attempt(inputs,boundary):
    root,*arguments = inputs
    def fail(stage):
        if stage == boundary:
            raise RuntimeError('simulated process interruption')
    with pytest.raises(RuntimeError):
        capture_attempt(root,*arguments,StubProvider(),checkpoint=fail)
    directory = next((root/'attempts').iterdir())
    assert (directory/'attempt.json').exists() and not (directory/'ready.json').exists()
    with pytest.raises(FileNotFoundError):
        verify_attempt(root,directory.name)


def test_published_attempt_tamper_is_detected(inputs):
    root,*arguments = inputs
    result = capture_attempt(root,*arguments,StubProvider())
    path = root/'attempts'/result['proposal_id']/'manifest.json'
    value = json.loads(path.read_bytes())
    value['provider_status'] = 'MODEL_TIMEOUT'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='checksum'):
        verify_attempt(root,result['proposal_id'])


def test_deadline_kills_the_actual_provider_process(tmp_path):
    marker = tmp_path/'should-not-exist'
    command = (sys.executable,'-c',f'import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text("late")')
    started = time.monotonic()
    result = run_child(command,tmp_path/'request.json',tmp_path,.05)
    assert result.status == 'MODEL_TIMEOUT' and result.response is None
    assert time.monotonic()-started < 2
    time.sleep(1.1)
    assert not marker.exists()


def test_missing_provider_and_model_are_inspectable(tmp_path):
    result = run_child(('/nonexistent/provider',),tmp_path/'request.json',tmp_path,1)
    assert result.status == 'MODEL_UNAVAILABLE' and result.response is None
    with pytest.raises(FileNotFoundError):
        verified_snapshot(tmp_path,PROFILE)


def test_source_hash_mismatch_cannot_become_an_attempt(inputs):
    root,image,source,instruction,vocabulary = inputs
    with pytest.raises(ValueError,match='checksum'):
        capture_attempt(root,image+b'changed',source,instruction,vocabulary,StubProvider())
    assert not (root/'attempts').exists()


def test_provider_exception_becomes_error_without_exposing_message(inputs):
    class Broken(StubProvider):
        def infer(self,*args):
            raise RuntimeError('private diagnostic text')
    root,*arguments = inputs
    result = capture_attempt(root,*arguments,Broken())
    assert result['provider_status'] == 'MODEL_ERROR'
    assert result['provider_metadata'] == {'error_type':'RuntimeError'}
    assert 'private diagnostic text' not in json.dumps(result)
