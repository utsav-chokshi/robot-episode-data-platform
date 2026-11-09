from pathlib import Path
import pytest
from robotics_data_platform.benchmark.run import once,completed,plan


def test_completed_slot_never_executes_twice(tmp_path):
    calls=[]
    intent={'case_id':'test','seed':17,'mode':'BASELINE_STRUCTURED'}
    def execute():
        calls.append(1)
        return {'episode_id':'test'}
    first=once(tmp_path,intent,execute,lambda:None)
    assert once(tmp_path,intent,lambda:pytest.fail('Cannot repeat execution'),lambda:None)==first
    assert len(calls)==1


def test_interrupted_slot_does_not_retry_without_final_evidence(tmp_path):
    def crash():raise RuntimeError('interrupted')
    intent={'case_id':'test'}
    with pytest.raises(RuntimeError):once(tmp_path,intent,crash,lambda:None)
    with pytest.raises(RuntimeError,match='automatic execution retry is forbidden'):
        once(tmp_path,intent,lambda:pytest.fail('Must not run again'),lambda:None)


def test_final_evidence_reconciles_without_reexecution(tmp_path):
    def crash():raise RuntimeError('after durable capture')
    intent={'case_id':'test'}
    with pytest.raises(RuntimeError):once(tmp_path,intent,crash,lambda:None)
    value=once(tmp_path,intent,lambda:pytest.fail('Must not run'),lambda:{'proposal_id':'original'})
    assert value['reconciled'] and value['attempt']['proposal_id']=='original'


def test_changed_binding_and_journal_corruption_are_rejected(tmp_path):
    once(tmp_path,{'seed':17},lambda:{'episode_id':'one'},lambda:None)
    with pytest.raises(ValueError,match='binding'):
        once(tmp_path,{'seed':23},lambda:pytest.fail(),lambda:None)
    path=tmp_path/'complete.json'
    path.write_bytes(path.read_bytes().replace(b'one',b'two'))
    with pytest.raises(ValueError,match='checksum'):completed(tmp_path)


def test_plan_binds_exact_frozen_taskset(tmp_path):
    task,value=plan(tmp_path,Path('examples/evaluation/fixed-task-set.json'))
    assert len(task.cases)==30 and value['task_set_sha256']=='4e83bc1aac993c668daddc21196b16bf0210a45010185fca6f05b90c33116643'
    import json
    changed=task.model_dump()
    changed['disclosure']+=' Changed reference.'
    path=tmp_path/'changed.json'
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='frozen'):
        plan(tmp_path/'different',path)
