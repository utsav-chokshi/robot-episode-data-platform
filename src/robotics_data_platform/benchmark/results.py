"""Data-only publication and measured benchmark summary; never executes attempts."""
import json,statistics
from collections import Counter
from pathlib import Path
from sqlalchemy import text
from robotics_data_platform.benchmark.run import plan,completed,assignments
from robotics_data_platform.analytics.snapshot import freeze_inputs
from robotics_data_platform.analytics.build import publish,rebuild
from robotics_data_platform.evaluation.contracts import Assignments
from robotics_data_platform.evaluation.reports import publish_report
from robotics_data_platform.database import build_engine
from robotics_data_platform.settings import Settings
from robotics_data_platform.ingestion.storage import atomic_write,canonical


def summary(root):
    baseline=[completed(path) for path in (root/'baseline').iterdir()]
    inference=[completed(path) for path in (root/'inference').iterdir()]
    def distribution(values):
        values=sorted(value for value in values if value is not None)
        if not values:return {'sample_count':0,'p50':None,'p95':None,'max':None}
        # Nearest-rank percentiles, without interpolation over missing durations.
        import math
        return {'sample_count':len(values),'p50':values[math.ceil(.5*len(values))-1],'p95':values[math.ceil(.95*len(values))-1],'max':max(values)}
    rows=[row['attempt'] for row in inference]
    completed_rows=[row for row in rows if row['provider_status']=='COMPLETED']
    phases={phase:distribution([row['provider_metadata'].get('worker',{}).get('timings_ms',{}).get(phase) for row in completed_rows]) for phase in ['load','preprocess','generation']}
    return {'baseline_attempts':len(baseline),'baseline_outcomes':dict(Counter(row['attempt']['outcome'] for row in baseline)),'model_attempts':len(rows),'provider_statuses':dict(Counter(row['provider_status'] for row in rows)),'schema_valid_completed':sum(row['validation']['schema_valid'] for row in completed_rows),'schema_invalid_completed':sum(not row['validation']['schema_valid'] for row in completed_rows),'model_wall_ms_all_attempts':distribution([row['provider_metadata'].get('wall_ms') for row in rows]),'model_wall_ms_completed':distribution([row['provider_metadata'].get('wall_ms') for row in completed_rows]),'phase_ms_completed':phases,'process_peak_rss_mib':distribution([row['provider_metadata'].get('worker',{}).get('process_peak_rss_mib') for row in completed_rows]),'baseline_wall_ms':distribution([row['wall_ms'] for row in baseline]),'percentile_method':'nearest-rank','limits':'Process RSS is not total container memory. Phase timings are completed-inference only; all-attempt wall includes failures/timeouts. Repetition seeds do not imply independent scenes.'}


def main():
    root=Path('/benchmark/fixed-v1')
    task,_=plan(root,Path('/app/examples/evaluation/fixed-task-set.json'))
    assignments(task,root)
    engine=build_engine(Settings())
    try:
        bound=Assignments.model_validate_json((root/'assignments.json').read_bytes())
        with engine.connect() as connection:
            for mode,table,key in [('BASELINE_STRUCTURED','episodes','episode_id'),('VLM_EVALUATION','plan_attempts','proposal_id')]:
                identities=[item.attempt_id for item in bound.bindings if item.mode==mode]
                count=connection.scalar(text(f'SELECT count(*) FROM {table} WHERE {key}=ANY(CAST(:ids AS uuid[]))'),{'ids':identities})
                if count!=len(identities):
                    raise ValueError('Benchmark source attempts must be ingested before publication')
        definition=json.loads((root/'baseline-definition.json').read_bytes())
        assignment=json.loads((root/'baseline-assignment.json').read_bytes())
        identity=freeze_inputs(engine,Path('/raw'),Path('/datasets'),definition,assignment,task_set_id='tabletop-evaluation-v1-structured',split='fixed_evaluation')
        dataset=publish(engine,Path('/raw'),Path('/datasets'),identity)
        report=publish_report(engine,Path('/datasets'),Path('/evaluations'),dataset['dataset_id'],task,bound)
        if report['result']['metrics']['VLM_EVALUATION']['coverage']['numerator']!=90 or report['result']['metrics']['BASELINE_STRUCTURED']['coverage']['numerator']!=54:
            raise ValueError('Benchmark has incomplete assigned coverage')
        value={'dataset':dataset,'evaluation':report,'measurements':summary(root),'rebuild':rebuild(Path('/raw'),Path('/datasets'),dataset['build_fingerprint'])}
        atomic_write(root/'publication.json',canonical(value))
        print(json.dumps({'dataset_id':dataset['dataset_id'],'report_id':report['report_id'],'metrics':report['result']['metrics'],'measurements':value['measurements']},sort_keys=True))
    finally:
        engine.dispose()


if __name__=='__main__':main()
