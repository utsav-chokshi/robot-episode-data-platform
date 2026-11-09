"""Deterministic typed tables and development-set SQL metrics."""
import json
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from robotics_data_platform.analytics.files import digest,file_hash

SCHEMAS = {
 'episodes': [('episode_id','s'),('seal_id','s'),('revision','i'),('quality','s'),('eligible','b'),('mode','s'),('object_id','s'),('destination_id','s'),('seed','i'),('source_fingerprint','s'),('outcome','s'),('failure_code','s')],
 'events': [('event_id','s'),('episode_id','s'),('event_type','s'),('producer_sequence','i'),('sha256','s'),('payload_json','s')],
 'artifacts': [('artifact_id','s'),('episode_id','s'),('role','s'),('media_type','s'),('sha256','s'),('byte_size','i')],
 'observations': [('observation_id','s'),('episode_id','s'),('event_id','s'),('image_artifact_id','s'),('camera_config_artifact_id','s'),('camera_id','s'),('width_px','i'),('height_px','i')],
 'actions': [('action_id','s'),('episode_id','s'),('primitive','s'),('object_id','s'),('destination_id','s'),('status','s'),('duration_ms','f'),('failure_code','s')],
 'findings': [('episode_id','s'),('code','s'),('message','s'),('resolved','b')],
 'evaluation_cases': [('case_id','s'),('episode_id','s'),('category','s'),('split','s'),('expected_outcome','s'),('expected_failure_code','s'),('eligible','b'),('request_matches','b'),('actual_outcome','s'),('actual_failure_code','s'),('expectation_match','b')],
}


def rows(snapshot):
    tables = {name:[] for name in SCHEMAS}
    by_id = {}
    for member in snapshot['members']:
        catalog,seal,bundle = member['catalog'],member['seal'],member['bundle']
        ep = catalog['episode_id']
        by_id[ep] = member
        request = member.get('auxiliary',{}).get('task_request') or {}
        tables['episodes'].append(dict(episode_id=ep,seal_id=seal['seal_id'],revision=seal['revision'],quality=seal['quality'],eligible=seal['eligible'],mode=catalog['mode'],object_id=request.get('object_id'),destination_id=request.get('destination_id'),seed=request.get('seed'),source_fingerprint=catalog['content_fingerprint'],outcome=catalog['outcome'],failure_code=catalog['failure_code']))
        for issue in seal['issues']:
            tables['findings'].append(dict(episode_id=ep,code=issue['code'],message=issue['message'],resolved=False))
        if not seal['eligible']:
            continue
        for event in bundle['events']:
            tables['events'].append({**event,'payload_json':json.dumps(event['payload'],sort_keys=True,separators=(',',':'))})
        tables['artifacts'].extend(bundle['artifacts'])
        for observation in bundle['observations']:
            tables['observations'].append({**observation['payload'],'event_id':observation['event_id'],'episode_id':ep})
        for key,start in bundle['starts'].items():
            finish = bundle['finishes'][key]['payload']
            tables['actions'].append({**start['payload'],**finish,'action_id':key,'episode_id':ep})
    for finding in snapshot['receiver_findings']:
        tables['findings'].append({**finding,'resolved':finding['resolved_by_receipt'] is not None})
    for slot in snapshot['task_set']['slots']:
        member = by_id.get(slot['episode_id'])
        request = (member.get('auxiliary',{}).get('task_request') or {}) if member else {}
        request_matches = bool(member) and all(request.get(k)==v for k,v in slot['request'].items())
        eligible = bool(member and member['seal']['eligible'] and request_matches)
        actual = member['bundle']['summary'] if eligible else {}
        tables['evaluation_cases'].append({**slot,'eligible':eligible,'request_matches':request_matches,'actual_outcome':actual.get('outcome'),'actual_failure_code':actual.get('failure_code'),'expectation_match':eligible and actual.get('outcome')==slot['expected_outcome'] and actual.get('failure_code')==slot['expected_failure_code']})
    result = {}
    for name,fields in SCHEMAS.items():
        projected = [{key:row.get(key) for key,_ in fields} for row in tables[name]]
        result[name] = sorted(projected,key=lambda row:json.dumps(row,sort_keys=True))
    return result


def materialize(snapshot,directory):
    directory.mkdir(parents=True,exist_ok=True)
    types = {'s':pa.string(),'i':pa.int64(),'b':pa.bool_(),'f':pa.float64()}
    tables = rows(snapshot)
    descriptors = {}
    connection = duckdb.connect(':memory:')
    connection.execute("SET threads=2")
    connection.execute("SET memory_limit='512MB'")
    for name,fields in SCHEMAS.items():
        table = pa.Table.from_pylist(tables[name],schema=pa.schema([(key,types[kind]) for key,kind in fields]))
        path = directory/(name+'.parquet')
        pq.write_table(table,path,compression='zstd')
        connection.read_parquet(str(path)).create_view(name)
        descriptors[name] = {'rows':len(tables[name]),'logical_sha256':digest(tables[name]),'file_sha256':file_hash(path),'schema':str(table.schema)}
    checks = {
      'unique_episodes':"SELECT count(*)-count(DISTINCT episode_id) FROM episodes",
      'unique_events':"SELECT count(*)-count(DISTINCT event_id) FROM events",
      'unique_artifacts':"SELECT count(*)-count(DISTINCT artifact_id) FROM artifacts",
      'unique_expected_cases':"SELECT count(*)-count(DISTINCT case_id) FROM evaluation_cases",
      'facts_have_eligible_episode':"SELECT count(*) FROM events e LEFT JOIN episodes p USING(episode_id) WHERE p.episode_id IS NULL OR NOT p.eligible",
      'observation_images_exist':"SELECT count(*) FROM observations o LEFT JOIN artifacts a ON o.image_artifact_id=a.artifact_id AND o.episode_id=a.episode_id WHERE a.artifact_id IS NULL",
    }
    quality = {name:connection.execute(sql).fetchone()[0] for name,sql in checks.items()}
    if any(quality.values()):
        raise ValueError('Analytical quality assertion failed: '+json.dumps(quality))
    metrics = {'split':'development','quality_violations':quality,'catalog_episodes':len(tables['episodes']),'eligible_episodes':sum(r['eligible'] for r in tables['episodes'])}
    for label,where,numerator in [
      ('coverage','TRUE','eligible'),
      ('expectation_match','TRUE','expectation_match'),
      ('physical_task_success',"category IN ('normal','injected_fault')","eligible AND actual_outcome='SUCCEEDED'"),
      ('normal_task_success',"category='normal'","eligible AND actual_outcome='SUCCEEDED'"),
      ('rejection_match',"category='rejection'",'expectation_match')]:
        count,total = connection.execute(f'SELECT count(*) FILTER (WHERE {numerator}),count(*) FROM evaluation_cases WHERE {where}').fetchone()
        metrics[label] = {'numerator':count,'denominator':total,'rate':count/total if total else None}
    connection.close()
    return descriptors,metrics
