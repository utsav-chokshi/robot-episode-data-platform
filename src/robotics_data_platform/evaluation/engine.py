"""Evaluate frozen evidence only; reference labels never reach a model."""
from collections import Counter
import hashlib

VERSION='evaluation-v1'


def rate(n,d):
    return {'numerator':n,'denominator':d,'rate':n/d if d else None}


def evaluate(snapshot,task_set,assignments):
    cases={case.case_id:case for case in task_set.cases}
    bound={}
    used=set()
    for item in assignments.bindings:
        if item.case_id not in cases or item.seed not in cases[item.case_id].seeds:
            raise ValueError('Assignment is outside the frozen task set')
        key=(item.case_id,item.seed,item.mode)
        if key in bound or (item.mode,item.attempt_id) in used:
            raise ValueError('Duplicate slot or reused attempt assignment')
        if item.mode=='BASELINE_STRUCTURED' and cases[item.case_id].baseline_request is None:
            raise ValueError('Assignment targets an inapplicable baseline case')
        if item.mode=='VLM_EVALUATION' and not cases[item.case_id].vlm_applicable:
            raise ValueError('Assignment targets an inapplicable VLM case')
        bound[key]=item.attempt_id
        used.add((item.mode,item.attempt_id))
    baselines={member['catalog']['episode_id']:member for member in snapshot['members']}
    proposals={member['proposal_id']:member for member in snapshot.get('proposals',{}).get('members',[])}
    rows=[]
    for case in task_set.cases:
        for seed in case.seeds:
            for mode in ['BASELINE_STRUCTURED','VLM_EVALUATION']:
                applicable=case.vlm_applicable if mode=='VLM_EVALUATION' else case.baseline_request is not None
                identity=bound.get((case.case_id,seed,mode))
                row={'case_id':case.case_id,'seed':seed,'mode':mode,'category':case.category,'instruction':case.instruction,'applicable':applicable,'attempt_id':identity,'captured':False,'evidence_eligible':False,'schema_valid':None,'reference_valid':None,'disposition':None,'expectation_match':False,'physical_success':None,'failure_code':'NOT_APPLICABLE' if not applicable else 'MISSING_ATTEMPT','wall_ms':None}
                if not applicable or identity is None:
                    rows.append(row)
                    continue
                member=(baselines if mode=='BASELINE_STRUCTURED' else proposals).get(identity)
                if member is None:
                    row['failure_code']='ATTEMPT_NOT_IN_DATASET'
                    rows.append(row)
                    continue
                row['captured']=True
                eligible=member['seal']['eligible'] if mode=='BASELINE_STRUCTURED' else member['eligible']
                if not eligible:
                    row['failure_code']='INCOMPLETE_EVIDENCE'
                    rows.append(row)
                    continue
                if mode=='BASELINE_STRUCTURED':
                    request=member.get('auxiliary',{}).get('task_request') or {}
                    expected_request={**case.baseline_request,'seed':seed}
                    # Older rejected CLI requests omit seed; TaskInput's declared default is 7.
                    actual={**request,'seed':request.get('seed',7)}
                    if any(actual.get(k)!=v for k,v in expected_request.items()):
                        row['failure_code']='INPUT_BINDING_MISMATCH'
                        rows.append(row)
                        continue
                    summary=member['bundle']['summary']
                    row['evidence_eligible']=True
                    row['disposition']=summary['outcome']
                    row['expectation_match']=summary['outcome']==case.baseline_outcome and summary['failure_code']==case.baseline_failure_code
                    if case.baseline_outcome in {'SUCCEEDED','FAILED'}:
                        row['physical_success']=summary['outcome']=='SUCCEEDED'
                    row['failure_code']='NONE' if row['expectation_match'] else summary['failure_code'] or 'BASELINE_OUTCOME_MISMATCH'
                    # Expected fault still has an execution failure, separately from expectation matching.
                    if summary['outcome']=='FAILED':
                        row['failure_code']=summary['failure_code'] or 'EXECUTION_FAILED'
                else:
                    manifest=member['bundle']['manifest']
                    roles={item['role']:item for item in manifest['artifacts']}
                    source=baselines.get(manifest['source']['episode_id'])
                    if source is None or not source['seal']['eligible'] or source['bundle'] is None:
                        row['failure_code']='SOURCE_INCOMPLETE'
                        rows.append(row)
                        continue
                    source_request=(source.get('auxiliary',{}).get('task_request') or {}) if source else {}
                    observations=source['bundle']['observations'] if source and source['bundle'] else []
                    first=min(observations,key=lambda event:event['producer_sequence']) if observations else None
                    if roles['instruction']['sha256']!=hashlib.sha256(case.instruction.encode('utf-8')).hexdigest() or source_request.get('seed',7)!=seed or first is None or first['payload']['observation_id']!=manifest['source']['observation_id']:
                        row['failure_code']='INPUT_BINDING_MISMATCH'
                        rows.append(row)
                        continue
                    row['evidence_eligible']=True
                    row['wall_ms']=manifest['provider_metadata'].get('wall_ms')
                    if manifest['provider_status']!='COMPLETED':
                        row['failure_code']=manifest['provider_status']
                    else:
                        validation=member['bundle']['validation']
                        row['schema_valid']=validation['schema_valid']
                        row['reference_valid']=validation['reference_valid']
                        parsed=validation['parsed_proposal']
                        if not validation['schema_valid']:
                            row['failure_code']='PLAN_SCHEMA_INVALID'
                        elif validation['reference_valid'] is False:
                            row['failure_code']='REFERENCE_INVALID'
                        else:
                            row['disposition']=parsed['status']
                            expected=case.expected.model_dump(exclude_none=True)
                            row['expectation_match']=parsed==expected
                            row['failure_code']='NONE' if row['expectation_match'] else 'SEMANTIC_MISMATCH'
                rows.append(row)
    metrics={}
    for mode in ['BASELINE_STRUCTURED','VLM_EVALUATION']:
        selected=[row for row in rows if row['mode']==mode and row['applicable']]
        physical=[row for row in selected if cases[row['case_id']].baseline_outcome in {'SUCCEEDED','FAILED'}] if mode=='BASELINE_STRUCTURED' else []
        ready=[row for row in selected if cases[row['case_id']].expected.status=='ready']
        clarify=[row for row in selected if cases[row['case_id']].expected.status=='clarify']
        reject=[row for row in selected if cases[row['case_id']].expected.status=='reject']
        latencies=sorted(row['wall_ms'] for row in selected if row['wall_ms'] is not None)
        metrics[mode]={'applicable_slots':len(selected),'not_applicable':sum(row['mode']==mode and not row['applicable'] for row in rows),'coverage':rate(sum(row['captured'] for row in selected),len(selected)),'complete_evidence':rate(sum(row['evidence_eligible'] for row in selected),len(selected)),'expectation_match':rate(sum(row['expectation_match'] for row in selected),len(selected)),'physical_success':rate(sum(row['physical_success'] is True for row in physical),len(physical)) if mode=='BASELINE_STRUCTURED' else {'status':'NOT_APPLICABLE'},'schema_valid':rate(sum(row['schema_valid'] is True for row in selected),len(selected)) if mode=='VLM_EVALUATION' else {'status':'NOT_APPLICABLE'},'ready_match':rate(sum(row['expectation_match'] for row in ready),len(ready)),'clarify_match':rate(sum(row['expectation_match'] for row in clarify),len(clarify)),'reject_match':rate(sum(row['expectation_match'] for row in reject),len(reject)),'failure_counts':dict(sorted(Counter(row['failure_code'] for row in selected if row['failure_code']!='NONE').items())),'wall_latency_ms':{'sample_count':len(latencies),'min':min(latencies) if latencies else None,'max':max(latencies) if latencies else None,'mean':sum(latencies)/len(latencies) if latencies else None}}
    return {'evaluator_version':VERSION,'task_set':task_set.model_dump(),'rows':sorted(rows,key=lambda row:(row['case_id'],row['seed'],row['mode'])),'metrics':metrics}
