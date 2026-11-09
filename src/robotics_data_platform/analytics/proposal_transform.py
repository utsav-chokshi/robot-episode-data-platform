"""Inference audit tables; provider completion is separate from proposal validity."""
import json
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from robotics_data_platform.analytics.files import digest,file_hash

SCHEMAS={
 'proposals':[('proposal_id','s'),('episode_id','s'),('source_episode_id','s'),('source_observation_id','s'),('source_image_sha256','s'),('eligible','b'),('quality','s'),('provider_status','s'),('outcome','s'),('execution_eligible','b'),('model_id','s'),('model_revision','s'),('manifest_sha256','s'),('wall_ms','f')],
 'proposal_artifacts':[('artifact_id','s'),('proposal_id','s'),('episode_id','s'),('role','s'),('sha256','s'),('byte_size','i')],
 'proposal_validations':[('proposal_id','s'),('schema_valid','b'),('reference_valid','b'),('execution_eligible','b'),('disposition','s'),('reason_codes_json','s')],
 'proposal_receipts':[('receipt_sha256','s'),('proposal_id','s'),('manifest_sha256','s'),('status','s')],
 'proposal_findings':[('proposal_id','s'),('code','s'),('message','s'),('resolved','b')],
}


def materialize_proposals(snapshot,directory):
    values={name:[] for name in SCHEMAS}
    for member in snapshot['proposals']['members']:
        catalog=member['catalog'] or {}
        bundle=member['bundle']
        manifest=bundle['manifest'] if member['eligible'] else {}
        source=manifest.get('source',{})
        values['proposals'].append({'proposal_id':member['proposal_id'],'episode_id':catalog.get('episode_id'),'source_episode_id':catalog.get('source_episode_id'),'source_observation_id':catalog.get('source_observation_id'),'source_image_sha256':source.get('image_sha256',catalog.get('source_image_sha256')),'eligible':member['eligible'],'quality':member['quality'],'provider_status':catalog.get('provider_status'),'outcome':catalog.get('outcome'),'execution_eligible':False,'model_id':catalog.get('model_id'),'model_revision':catalog.get('model_revision'),'manifest_sha256':catalog.get('manifest_sha256'),'wall_ms':manifest.get('provider_metadata',{}).get('wall_ms')})
        for issue in member['issues']:
            values['proposal_findings'].append({'proposal_id':member['proposal_id'],**issue,'resolved':False})
        if not member['eligible']:
            continue
        for artifact in manifest['artifacts']:
            values['proposal_artifacts'].append({**artifact,'proposal_id':member['proposal_id']})
        validation=bundle['validation']
        if validation is not None:
            values['proposal_validations'].append({'proposal_id':member['proposal_id'],**validation,'disposition':(validation['parsed_proposal'] or {}).get('status'),'reason_codes_json':json.dumps(validation['reason_codes'],sort_keys=True)})
    values['proposal_receipts']=snapshot['proposals']['receipts']
    for finding in snapshot['proposals']['findings']:
        values['proposal_findings'].append({**finding,'resolved':finding['resolved_by_receipt'] is not None})
    descriptors={}
    types={'s':pa.string(),'b':pa.bool_(),'i':pa.int64(),'f':pa.float64()}
    with duckdb.connect(':memory:') as connection:
        connection.execute("SET threads=2")
        connection.execute("SET memory_limit='512MB'")
        for name,fields in SCHEMAS.items():
            rows=sorted([{key:row.get(key) for key,_ in fields} for row in values[name]],key=lambda value:json.dumps(value,sort_keys=True))
            table=pa.Table.from_pylist(rows,schema=pa.schema([(key,types[kind]) for key,kind in fields]))
            path=directory/(name+'.parquet')
            pq.write_table(table,path,compression='zstd')
            connection.read_parquet(str(path)).create_view(name)
            descriptors[name]={'rows':len(rows),'logical_sha256':digest(rows),'file_sha256':file_hash(path),'schema':str(table.schema)}
        checks={
         'unique_proposals':'SELECT count(*)-count(DISTINCT proposal_id) FROM proposals',
         'unique_proposal_artifacts':'SELECT count(*)-count(DISTINCT artifact_id) FROM proposal_artifacts',
         'proposal_artifact_parent':'SELECT count(*) FROM proposal_artifacts a LEFT JOIN proposals p USING(proposal_id) WHERE p.proposal_id IS NULL OR NOT p.eligible OR p.episode_id<>a.episode_id',
         'validation_parent':'SELECT count(*) FROM proposal_validations v LEFT JOIN proposals p USING(proposal_id) WHERE p.proposal_id IS NULL OR NOT p.eligible OR p.provider_status<>\'COMPLETED\'',
         'no_execution_eligibility':'SELECT count(*) FROM proposals WHERE execution_eligible',
        }
        violations={key:connection.execute(sql).fetchone()[0] for key,sql in checks.items()}
        if any(violations.values()):
            raise ValueError('Inference analytical quality failed')
        total=connection.execute('SELECT count(*) FROM proposals').fetchone()[0]
        eligible=connection.execute('SELECT count(*) FROM proposals WHERE eligible').fetchone()[0]
        completed=connection.execute("SELECT count(*) FROM proposals WHERE eligible AND provider_status='COMPLETED'").fetchone()[0]
        valid=connection.execute('SELECT count(*) FROM proposal_validations WHERE schema_valid').fetchone()[0]
        statuses=dict(connection.execute("SELECT coalesce(provider_status,'UNKNOWN'),count(*) FROM proposals GROUP BY provider_status").fetchall())
        def rate(n,d):
            return {'numerator':n,'denominator':d,'rate':n/d if d else None}
        metrics={'split':'development','attempts':total,'integrity_complete':rate(eligible,total),'provider_completion':rate(completed,total),'schema_valid_all_attempts':rate(valid,total),'schema_valid_completed':rate(valid,completed),'provider_status_counts':statuses,'execution_eligible':rate(0,total),'physical_success':{'status':'NOT_APPLICABLE','reason':'VLM_EVALUATION never executes robot actions'},'quality_violations':violations}
    if snapshot['task_set']['split']!='development':
        metrics['split']='mixed_audit_corpus'
    return descriptors,metrics
