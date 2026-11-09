"""Container commands; never imports the simulator execution path."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation',choices=['freeze','build','rebuild'])
    parser.add_argument('--identity')
    parser.add_argument('--raw-root',type=Path,default=Path('/raw'))
    parser.add_argument('--dataset-root',type=Path,default=Path('/datasets'))
    parser.add_argument('--definition',type=Path,default=Path('/app/examples/simulator-suite.json'))
    parser.add_argument('--assignment',type=Path,default=Path('/capture/suite-result.json'))
    args = parser.parse_args()
    from robotics_data_platform.analytics.build import publish,rebuild
    from robotics_data_platform.settings import Settings
    from robotics_data_platform.database import build_engine
    from sqlalchemy import text
    engine = build_engine(Settings())
    run_id = str(uuid4())
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO pipeline_runs(run_id,operation,input_identity,prefect_run_id,state) VALUES (:id,:operation,:input,:prefect,'RUNNING')"),{'id':run_id,'operation':args.operation,'input':args.identity,'prefect':os.environ.get('RDP_PREFECT_RUN_ID')})
        try:
            if args.operation == 'rebuild':
                result = rebuild(args.raw_root,args.dataset_root,args.identity)
            elif args.operation == 'freeze':
                from robotics_data_platform.analytics.snapshot import freeze_inputs
                identity = freeze_inputs(engine,args.raw_root,args.dataset_root,json.loads(args.definition.read_bytes()),json.loads(args.assignment.read_bytes()))
                result = {'input_sha256':identity}
            else:
                result = publish(engine,args.raw_root,args.dataset_root,args.identity)
        except Exception as error:
            with engine.begin() as connection:
                connection.execute(text("UPDATE pipeline_runs SET state='FAILED',error_type=:error,finished_at_utc=now() WHERE run_id=:id"),{'id':run_id,'error':type(error).__name__[:128]})
            raise
        with engine.begin() as connection:
            connection.execute(text("UPDATE pipeline_runs SET state='COMPLETED',result_identity=:result,finished_at_utc=now() WHERE run_id=:id"),{'id':run_id,'result':result.get('build_fingerprint',result.get('input_sha256'))})
    finally:
        engine.dispose()
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':
    main()
