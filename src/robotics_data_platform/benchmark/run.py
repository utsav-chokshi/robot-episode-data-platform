"""One declared attempt per frozen slot. Never silently retry execution."""
import argparse,json,platform,time
from pathlib import Path
from datetime import datetime,timezone
from robotics_data_platform.analytics.files import digest,file_hash,immutable_json
from robotics_data_platform.ingestion.storage import atomic_write,canonical,receiver_lock
from robotics_data_platform.evaluation.contracts import TaskSet,Assignments
from robotics_data_platform.planning.adapter import PROFILE

FROZEN_TASK_SHA='4e83bc1aac993c668daddc21196b16bf0210a45010185fca6f05b90c33116643'


def now():
    return datetime.now(timezone.utc).isoformat()


def plan(root,task_path):
    task=TaskSet.model_validate_json(task_path.read_bytes())
    task_sha=digest(task.model_dump())
    if task_sha!=FROZEN_TASK_SHA:
        raise ValueError('Task set differs from the frozen Step 10 reference')
    value={'benchmark_contract_version':'1.0.0','task_set_sha256':task_sha,'profile':PROFILE,'runner_sha256':file_hash(Path(__file__)),'task_set':task.model_dump()}
    immutable_json(root/'run.json',value)
    return task,value


def slot_key(case_id,seed,mode):
    return digest({'case_id':case_id,'seed':seed,'mode':mode})


def completed(directory):
    value=json.loads((directory/'complete.json').read_bytes())
    if digest(value['result'])!=value['result_sha256']:
        raise ValueError('Slot journal checksum mismatch')
    return value['result']


def once(directory,intent,execute,reconcile):
    directory.mkdir(parents=True,exist_ok=True)
    with receiver_lock(directory):
        if (directory/'complete.json').exists():
            result=completed(directory)
            if result['slot']!=intent:
                raise ValueError('Completed slot binding differs')
            return result
        if (directory/'intent.json').exists():
            if json.loads((directory/'intent.json').read_bytes())['slot']!=intent:
                raise ValueError('Interrupted slot binding differs')
            payload=reconcile()
            if payload is None:
                raise RuntimeError('Slot has an unfinished attempt. Investigate retained evidence; automatic execution retry is forbidden.')
            result={'slot':intent,'attempt':payload,'reconciled':True,'wall_ms':None}
        else:
            atomic_write(directory/'intent.json',canonical({'slot':intent,'started_at_utc':now()}))
            started=time.perf_counter()
            payload=execute()
            result={'slot':intent,'attempt':payload,'reconciled':False,'wall_ms':(time.perf_counter()-started)*1000}
        atomic_write(directory/'complete.json',canonical({'result':result,'result_sha256':digest(result)}))
        return result


def baseline(task,root,capture):
    from robotics_data_platform.simulator.runner import execute_request
    from robotics_data_platform.simulator.capture import verify_episode
    for case in task.cases:
        if case.baseline_request is None:
            continue
        for seed in case.seeds:
            key=slot_key(case.case_id,seed,'BASELINE_STRUCTURED')
            target=capture/'benchmark-v1'/key
            intent={'case_id':case.case_id,'seed':seed,'mode':'BASELINE_STRUCTURED','input_sha256':digest({**case.baseline_request,'seed':seed})}
            def recover():
                paths=list((target/'episodes').glob('*'))
                if len(paths)==1 and (paths[0]/'manifest.json').exists():
                    summary=verify_episode(paths[0])
                    return {'episode_id':summary['episode_id'],'capture_relative':paths[0].relative_to(capture).as_posix(),'outcome':summary['outcome'],'failure_code':summary['failure_code']}
                return None
            def execute():
                value=execute_request({**case.baseline_request,'seed':seed},target)
                path=target/'episodes'/value['episode_id']
                verify_episode(path)
                return {'episode_id':value['episode_id'],'capture_relative':path.relative_to(capture).as_posix(),'outcome':value['outcome'],'failure_code':value['failure_code']}
            result=once(root/'baseline'/key,intent,execute,recover)
            print(json.dumps({'phase':'baseline',**result}),flush=True)


def infer(task,root,raw,proposals,vocabulary_path):
    from robotics_data_platform.planning.attempts import Vocabulary,observation_source,capture_attempt,verify_attempt
    from robotics_data_platform.planning.adapter import SmolProvider
    from robotics_data_platform.ingestion.storage import RawStore
    from robotics_data_platform.ingestion.validation import validate_receipt
    sources={}
    # Freeze one initial image per seed from the first supported case, never based on model answers.
    first=next(case for case in task.cases if case.baseline_request is not None)
    store=RawStore(raw)
    for seed in first.seeds:
        row=completed(root/'baseline'/slot_key(first.case_id,seed,'BASELINE_STRUCTURED'))
        episode=row['attempt']['episode_id']
        bundle=None
        for path in sorted((raw/'receipts').glob('*.json')):
            receipt=store.read_receipt(path)
            if receipt['episode_id']==episode:
                bundle=validate_receipt(store,receipt)
                break
        if bundle is None:
            raise ValueError('Benchmark baseline source has not been ingested')
        observation=min(bundle['observations'],key=lambda event:event['producer_sequence'])
        _,lineage=observation_source(raw,episode,observation['payload']['observation_id'])
        sources[str(seed)]=lineage
    immutable_json(root/'source-images.json',sources)
    vocabulary=Vocabulary.model_validate_json(vocabulary_path.read_bytes())
    immutable_json(root/'public-vocabulary.json',vocabulary.model_dump())
    for case in task.cases:
        for seed in case.seeds:
            key=slot_key(case.case_id,seed,'VLM_EVALUATION')
            target=proposals/'benchmark-v1'/key
            intent={'case_id':case.case_id,'seed':seed,'mode':'VLM_EVALUATION','instruction_sha256':digest(case.instruction),'source':sources[str(seed)],'profile_sha256':digest(PROFILE),'timeout_seconds':120}
            def recover():
                paths=list((target/'attempts').glob('*'))
                if len(paths)==1 and (paths[0]/'ready.json').exists():
                    manifest=verify_attempt(target,paths[0].name)
                    return {'proposal_id':manifest['proposal_id'],'proposal_relative':target.relative_to(proposals).as_posix(),'provider_status':manifest['provider_status'],'validation':manifest['validation'],'provider_metadata':manifest['provider_metadata']}
                return None
            def execute():
                source=sources[str(seed)]
                image=store.get(source['image_sha256'])
                manifest=capture_attempt(target,image,source,case.instruction,vocabulary,SmolProvider(),120)
                return {'proposal_id':manifest['proposal_id'],'proposal_relative':target.relative_to(proposals).as_posix(),'provider_status':manifest['provider_status'],'validation':manifest['validation'],'provider_metadata':manifest['provider_metadata']}
            result=once(root/'inference'/key,intent,execute,recover)
            print(json.dumps({'phase':'inference',**result}),flush=True)


def ingest(root,capture,proposals,raw,phase):
    from robotics_data_platform.database import build_engine
    from robotics_data_platform.settings import Settings
    from robotics_data_platform.ingestion.worker import Receiver
    from robotics_data_platform.planning.ingestion import ProposalReceiver
    engine=build_engine(Settings())
    try:
        results=[]
        for directory in sorted((root/phase).glob('*')):
            row=completed(directory)
            if phase=='baseline':
                path=(capture/row['attempt']['capture_relative']).resolve()
                if not path.is_relative_to(capture.resolve()):raise ValueError('Capture escapes managed root')
                value=Receiver(engine,raw).ingest(path)
            else:
                path=(proposals/row['attempt']['proposal_relative']).resolve()
                if not path.is_relative_to(proposals.resolve()):raise ValueError('Proposal escapes managed root')
                value=ProposalReceiver(engine,raw).ingest(path,row['attempt']['proposal_id'])
            results.append(value)
            if value['status'] not in {'INGESTED','DUPLICATE'}:
                raise RuntimeError('Data ingestion requires attention: '+json.dumps(value))
        atomic_write(root/(phase+'-ingestion.json'),canonical(results))
        print(json.dumps(results),flush=True)
    finally:
        engine.dispose()


def assignments(task,root):
    bindings=[]
    definitions=[]
    results=[]
    for case in task.cases:
        for seed in case.seeds:
            for mode,phase in [('BASELINE_STRUCTURED','baseline'),('VLM_EVALUATION','inference')]:
                if mode=='BASELINE_STRUCTURED' and case.baseline_request is None:continue
                path=root/phase/slot_key(case.case_id,seed,mode)
                if not (path/'complete.json').exists():continue
                value=completed(path)['attempt']
                identity=value['episode_id' if phase=='baseline' else 'proposal_id']
                bindings.append({'case_id':case.case_id,'seed':seed,'mode':mode,'attempt_id':identity})
                if mode=='BASELINE_STRUCTURED':
                    definition={'case_id':case.case_id+'-seed'+str(seed),'request':{**case.baseline_request,'seed':seed},'expected_outcome':case.baseline_outcome,'expected_failure_code':case.baseline_failure_code}
                    definitions.append(definition)
                    results.append({**definition,'actual':{'episode_id':identity}})
    if len(bindings)!=144:raise ValueError('Benchmark still has unfinished slots')
    assignment=Assignments.model_validate({'bindings':bindings})
    immutable_json(root/'assignments.json',assignment.model_dump())
    immutable_json(root/'baseline-definition.json',{'cases':definitions})
    immutable_json(root/'baseline-assignment.json',{'results':results})
    print(json.dumps({'bindings':len(bindings),'baseline':len(definitions),'model':len(bindings)-len(definitions)}))


def status(root):
    value={}
    for phase in ['baseline','inference']:
        directories=list((root/phase).glob('*'))
        rows=[completed(path) for path in directories if (path/'complete.json').exists()]
        value[phase]={'completed':len(rows),'unfinished':sum((p/'intent.json').exists() and not (p/'complete.json').exists() for p in directories),'results':{}}
        for row in rows:
            key=row['attempt'].get('outcome',row['attempt'].get('provider_status'))
            value[phase]['results'][key]=value[phase]['results'].get(key,0)+1
    print(json.dumps(value),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['plan','baseline','infer','ingest-baseline','ingest-inference','assignments','status'])
    parser.add_argument('--task-set',type=Path,default=Path('/app/examples/evaluation/fixed-task-set.json'))
    parser.add_argument('--root',type=Path,default=Path('/benchmark/fixed-v1'))
    parser.add_argument('--capture-root',type=Path,default=Path('/capture'))
    parser.add_argument('--proposal-root',type=Path,default=Path('/proposals'))
    parser.add_argument('--raw-root',type=Path,default=Path('/raw'))
    parser.add_argument('--vocabulary',type=Path,default=Path('/app/examples/proposals/vocabulary.json'))
    args=parser.parse_args()
    task,_=plan(args.root,args.task_set)
    if args.phase=='baseline':baseline(task,args.root,args.capture_root)
    elif args.phase=='infer':infer(task,args.root,args.raw_root,args.proposal_root,args.vocabulary)
    elif args.phase.startswith('ingest-'):ingest(args.root,args.capture_root,args.proposal_root,args.raw_root,'baseline' if args.phase=='ingest-baseline' else 'inference')
    elif args.phase=='assignments':assignments(task,args.root)
    else:status(args.root)


if __name__=='__main__':main()
