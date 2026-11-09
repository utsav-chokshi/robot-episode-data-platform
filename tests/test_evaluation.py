import copy,hashlib,json
from pathlib import Path
import pytest
from robotics_data_platform.evaluation.contracts import TaskSet,Assignments
from robotics_data_platform.evaluation.engine import evaluate


def inputs():
    instruction='Move the red package to zone A.'
    parsed={'status':'ready','object_id':'package_01','destination_id':'zone_a'}
    task=TaskSet.model_validate({'name':'test-reference-v1','split':'planned_evaluation','disclosure':'Synthetic tests; not robot/model benchmark measurements.','cases':[{'case_id':'red','category':'supported','instruction':instruction,'expected':parsed,'baseline_request':{'object_id':'package_01','destination_id':'zone_a'},'baseline_outcome':'SUCCEEDED','seeds':[17,23,29]}]})
    baseline={'catalog':{'episode_id':'robot'},'seal':{'eligible':True},'auxiliary':{'task_request':{'object_id':'package_01','destination_id':'zone_a','seed':17}},'bundle':{'summary':{'outcome':'SUCCEEDED','failure_code':None},'observations':[{'producer_sequence':2,'payload':{'observation_id':'initial'}}]}}
    proposal={'proposal_id':'model','eligible':True,'bundle':{'manifest':{'source':{'episode_id':'robot','observation_id':'initial'},'artifacts':[{'role':'instruction','sha256':hashlib.sha256(instruction.encode()).hexdigest()}],'provider_status':'COMPLETED','provider_metadata':{'wall_ms':12}},'validation':{'schema_valid':True,'reference_valid':True,'parsed_proposal':parsed}}}
    snapshot={'members':[baseline],'proposals':{'members':[proposal]}}
    assignments=Assignments.model_validate({'bindings':[{'case_id':'red','seed':17,'mode':mode,'attempt_id':identity} for mode,identity in [('BASELINE_STRUCTURED','robot'),('VLM_EVALUATION','model')]]})
    return snapshot,task,assignments


def test_missing_slots_stay_in_both_denominators():
    result=evaluate(*inputs())
    assert result['metrics']['BASELINE_STRUCTURED']['physical_success']=={'numerator':1,'denominator':3,'rate':1/3}
    assert result['metrics']['VLM_EVALUATION']['ready_match']['denominator']==3
    assert result['metrics']['VLM_EVALUATION']['coverage']['numerator']==1
    assert result['metrics']['VLM_EVALUATION']['physical_success']=={'status':'NOT_APPLICABLE'}


@pytest.mark.parametrize('problem',['instruction','seed','image','source'])
def test_input_binding_and_source_quality_precede_semantic_credit(problem):
    snapshot,task,assignments=inputs()
    if problem=='instruction':
        snapshot['proposals']['members'][0]['bundle']['manifest']['artifacts'][0]['sha256']='0'*64
    elif problem=='seed':
        snapshot['members'][0]['auxiliary']['task_request']['seed']=99
    elif problem=='image':
        snapshot['proposals']['members'][0]['bundle']['manifest']['source']['observation_id']='after-placement'
    else:
        snapshot['members'][0]['seal']['eligible']=False
    result=evaluate(snapshot,task,assignments)
    row=next(row for row in result['rows'] if row['mode']=='VLM_EVALUATION' and row['seed']==17)
    assert not row['expectation_match'] and not row['evidence_eligible']
    assert row['failure_code']==('SOURCE_INCOMPLETE' if problem=='source' else 'INPUT_BINDING_MISMATCH')


@pytest.mark.parametrize('status',['MODEL_TIMEOUT','MODEL_UNAVAILABLE','MODEL_ERROR'])
def test_provider_failures_are_not_model_rejections_or_schema_assessments(status):
    snapshot,task,assignments=inputs()
    snapshot['proposals']['members'][0]['bundle']['manifest']['provider_status']=status
    snapshot['proposals']['members'][0]['bundle']['validation']=None
    row=next(r for r in evaluate(snapshot,task,assignments)['rows'] if r['mode']=='VLM_EVALUATION' and r['seed']==17)
    assert row['schema_valid'] is None and row['disposition'] is None and row['failure_code']==status


def test_duplicate_attempt_cannot_fill_two_slots():
    snapshot,task,assignments=inputs()
    values=assignments.model_dump()
    values['bindings'].append({'case_id':'red','seed':23,'mode':'VLM_EVALUATION','attempt_id':'model'})
    with pytest.raises(ValueError,match='reused'):
        evaluate(snapshot,task,Assignments.model_validate(values))


def test_schema_valid_wrong_selection_is_not_a_correct_plan():
    snapshot,task,assignments=inputs()
    snapshot['proposals']['members'][0]['bundle']['validation']['parsed_proposal']={'status':'ready','object_id':'package_02','destination_id':'zone_a'}
    row=next(r for r in evaluate(snapshot,task,assignments)['rows'] if r['mode']=='VLM_EVALUATION' and r['seed']==17)
    assert row['schema_valid'] and not row['expectation_match'] and row['failure_code']=='SEMANTIC_MISMATCH'


def test_fixed_definition_has_90_vlm_and_54_baseline_slots_without_executing():
    task=TaskSet.model_validate_json(Path('examples/evaluation/fixed-task-set.json').read_bytes())
    result=evaluate({'members':[]},task,Assignments(bindings=[]))
    assert len(task.cases)==30
    assert result['metrics']['VLM_EVALUATION']['coverage']['denominator']==90
    assert result['metrics']['BASELINE_STRUCTURED']['coverage']['denominator']==54
    assert result['metrics']['BASELINE_STRUCTURED']['not_applicable']==36
    assert result['metrics']['VLM_EVALUATION']['coverage']['numerator']==0
