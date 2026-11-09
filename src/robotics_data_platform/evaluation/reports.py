"""Immutable evaluation reports registered against a verified dataset version."""
import json,os,tempfile
from pathlib import Path
from uuid import NAMESPACE_URL,uuid5
from sqlalchemy import text
from robotics_data_platform.analytics.files import digest,file_hash,immutable_json,load_input,verify_published,checked_hash
from robotics_data_platform.ingestion.storage import receiver_lock
from robotics_data_platform.evaluation.engine import evaluate
from robotics_data_platform.evaluation.contracts import TaskSet,Assignments


def verify_report(root,fingerprint,manifest_sha):
    directory=root/'reports'/checked_hash(fingerprint)
    manifest_path=directory/'manifest.json'
    if file_hash(manifest_path)!=manifest_sha:
        raise ValueError('Evaluation manifest checksum mismatch')
    manifest=json.loads(manifest_path.read_bytes())
    if manifest['fingerprint']!=fingerprint:
        raise ValueError('Evaluation identity mismatch')
    for filename in ['result.json','task-set.json','assignments.json']:
        if file_hash(directory/filename)!=manifest['files'][filename]:
            raise ValueError('Evaluation artifact checksum mismatch')
    result=json.loads((directory/'result.json').read_bytes())
    return {**manifest,'result':result}


def publish_report(engine,dataset_root,root,dataset_id,task_set,assignments):
    with engine.connect() as connection:
        dataset=connection.execute(text('SELECT build_fingerprint,manifest_sha256 FROM dataset_versions WHERE dataset_id=:id'),{'id':dataset_id}).mappings().one()
    source=verify_published(dataset_root,dataset['build_fingerprint'],dataset['manifest_sha256'])
    snapshot=load_input(dataset_root,source['input_sha256'])
    task_digest=digest(task_set.model_dump())
    assignment_digest=digest(assignments.model_dump())
    code={name:file_hash(Path(__file__).with_name(name)) for name in ['engine.py','contracts.py','reports.py']}
    fingerprint=digest({'dataset_id':str(dataset_id),'dataset_manifest_sha256':dataset['manifest_sha256'],'task_set_sha256':task_digest,'assignment_sha256':assignment_digest,'code':code})
    report_id=str(uuid5(NAMESPACE_URL,'robotics-evaluation:'+fingerprint))
    root.mkdir(parents=True,exist_ok=True)
    with receiver_lock(root):
        destination=root/'reports'/fingerprint
        with engine.connect() as connection:
            registered=connection.execute(text('SELECT manifest_sha256 FROM evaluation_runs WHERE fingerprint=:fp'),{'fp':fingerprint}).scalar_one_or_none()
        if registered and not destination.exists():
            raise ValueError('Registered evaluation files are missing')
        if destination.exists():
            checksum=registered or file_hash(destination/'manifest.json')
            report=verify_report(root,fingerprint,checksum)
            manifest={key:value for key,value in report.items() if key!='result'}
        else:
            result=evaluate(snapshot,task_set,assignments)
            (root/'staging').mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root/'staging') as temporary:
                staging=Path(temporary)
                for filename,value in [('result.json',result),('task-set.json',task_set.model_dump()),('assignments.json',assignments.model_dump())]:
                    immutable_json(staging/filename,value)
                manifest={'report_contract_version':'1.0.0','report_id':report_id,'fingerprint':fingerprint,'dataset_id':str(dataset_id),'dataset_manifest_sha256':dataset['manifest_sha256'],'task_set_name':task_set.name,'split':task_set.split,'task_set_sha256':task_digest,'assignment_sha256':assignment_digest,'files':{name:file_hash(staging/name) for name in ['result.json','task-set.json','assignments.json']}}
                immutable_json(staging/'manifest.json',manifest)
                destination.parent.mkdir(exist_ok=True)
                os.rename(staging,destination)
                descriptor=os.open(destination.parent,os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        checksum=file_hash(destination/'manifest.json')
        with engine.begin() as connection:
            connection.execute(text('INSERT INTO evaluation_runs(report_id,fingerprint,dataset_id,task_set_name,split,manifest_sha256,manifest) VALUES (:id,:fp,:dataset,:name,:split,:sha,CAST(:body AS jsonb)) ON CONFLICT(fingerprint) DO NOTHING'),{'id':report_id,'fp':fingerprint,'dataset':dataset_id,'name':task_set.name,'split':task_set.split,'sha':checksum,'body':json.dumps(manifest)})
        return verify_report(root,fingerprint,checksum)


def main():
    import argparse
    from robotics_data_platform.database import build_engine
    from robotics_data_platform.settings import Settings
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset-id',required=True)
    parser.add_argument('--task-set',type=Path,required=True)
    parser.add_argument('--assignments',type=Path,required=True)
    parser.add_argument('--dataset-root',type=Path,default=Path('/datasets'))
    parser.add_argument('--evaluation-root',type=Path,default=Path('/evaluations'))
    args=parser.parse_args()
    engine=build_engine(Settings())
    try:
        report=publish_report(engine,args.dataset_root,args.evaluation_root,args.dataset_id,TaskSet.model_validate_json(args.task_set.read_bytes()),Assignments.model_validate_json(args.assignments.read_bytes()))
        print(json.dumps(report,sort_keys=True))
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
