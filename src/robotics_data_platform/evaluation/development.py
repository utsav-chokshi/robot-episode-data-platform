"""Explicit retrospective cohort, never labeled as a held-out benchmark."""
import argparse,json,hashlib
from pathlib import Path
from sqlalchemy import text
from robotics_data_platform.analytics.files import verify_published,load_input,immutable_json
from robotics_data_platform.database import build_engine
from robotics_data_platform.settings import Settings
from robotics_data_platform.evaluation.contracts import TaskSet,Assignments


def prepare(snapshot):
    cases,bindings=[],[]
    for slot in snapshot['task_set']['slots']:
        request=slot['request']
        seed=request.get('seed',7)
        case_id='baseline-'+slot['case_id']
        outcome=slot['expected_outcome']
        category='fault' if outcome=='FAILED' else 'rejection' if outcome=='REJECTED' else 'supported'
        expected={'status':'ready','object_id':request.get('object_id','package_01'),'destination_id':request.get('destination_id','zone_a')} if outcome!='REJECTED' else {'status':'reject','reason_code':'UNSUPPORTED_INSTRUCTION'}
        cases.append({'case_id':case_id,'category':category,'instruction':'Structured baseline reference: '+slot['case_id'],'expected':expected,'vlm_applicable':False,'baseline_request':{k:v for k,v in request.items() if k!='seed'},'baseline_outcome':outcome,'baseline_failure_code':slot['expected_failure_code'],'seeds':[seed]})
        if slot['episode_id']:
            bindings.append({'case_id':case_id,'seed':seed,'mode':'BASELINE_STRUCTURED','attempt_id':slot['episode_id']})
    instructions={'red':'Move the red package to zone A.','yellow':'Move the yellow package to zone A.'}
    for key,color in [('red-1','red'),('red-2','red'),('yellow','yellow')]:
        cases.append({'case_id':'vlm-'+key,'category':'supported' if color=='red' else 'invalid','instruction':instructions[color],'expected':{'status':'ready','object_id':'package_01','destination_id':'zone_a'} if color=='red' else {'status':'reject','reason_code':'OBJECT_UNAVAILABLE'},'vlm_applicable':True,'baseline_request':None,'baseline_outcome':None,'baseline_failure_code':None,'seeds':[7]})
    groups={'red':[],'yellow':[]}
    for member in snapshot.get('proposals',{}).get('members',[]):
        catalog=member['catalog']
        if not catalog:
            raise ValueError('Retrospective cohort contains unaccepted identities; define an explicit task assignment')
        roles={a['role']:a for a in catalog['manifest']['artifacts']}
        matches=[color for color,instruction in instructions.items() if hashlib.sha256(instruction.encode()).hexdigest()==roles['instruction']['sha256']]
        if len(matches)!=1:
            raise ValueError('Development instructions changed; define a new task-set version')
        groups[matches[0]].append(member['proposal_id'])
    if len(groups['red'])>2 or len(groups['yellow'])>1:
        raise ValueError('Development cohort grew; no success-based subset selection is allowed')
    for color,identities in groups.items():
        for index,identity in enumerate(sorted(identities)):
            bindings.append({'case_id':'vlm-'+('red-'+str(index+1) if color=='red' else 'yellow'),'seed':7,'mode':'VLM_EVALUATION','attempt_id':identity})
    task=TaskSet.model_validate({'name':'development-smoke-v1','split':'development_retrospective','disclosure':'Retrospective development labels for previously captured smoke data. Seventeen structured cases and three VLM slots are separate, unpaired cohorts. VLM labels depend on intended instruction, never model output or provider status. Both red attempts, including the deliberate timeout, remain included. This is not a held-out accuracy estimate.','cases':cases})
    return task,Assignments.model_validate({'bindings':bindings})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset-id',required=True)
    parser.add_argument('--output-root',type=Path,default=Path('/evaluations/definitions/development-smoke-v1'))
    args=parser.parse_args()
    engine=build_engine(Settings())
    try:
        with engine.connect() as connection:
            row=connection.execute(text('SELECT build_fingerprint,manifest_sha256 FROM dataset_versions WHERE dataset_id=:id'),{'id':args.dataset_id}).mappings().one()
        manifest=verify_published(Path('/datasets'),row['build_fingerprint'],row['manifest_sha256'])
        task,assignment=prepare(load_input(Path('/datasets'),manifest['input_sha256']))
        immutable_json(args.output_root/'task-set.json',task.model_dump())
        immutable_json(args.output_root/'assignments.json',assignment.model_dump())
        print(json.dumps({'task_set':str(args.output_root/'task-set.json'),'assignments':str(args.output_root/'assignments.json')}))
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
