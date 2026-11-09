"""Robotics Evidence Lab — entirely read-only."""
import json
import streamlit as st
from robotics_data_platform.dashboard import client as api

st.set_page_config(page_title='Robotics Evidence Lab',page_icon='◈',layout='wide')


@st.cache_data(ttl=5,show_spinner=False)
def data(path):
    return api.read(path)


@st.cache_data(ttl=5,show_spinner=False)
def binary(path):
    return api.read(path,True)


def fraction(value):
    if not value or 'denominator' not in value or value['denominator']==0:
        return 'N/A'
    return f"{value['numerator']} / {value['denominator']}"


def intro(kicker,title,description):
    st.caption(kicker.upper())
    st.title(title)
    st.write(description)


def overview(manifest):
    intro('Robotics data · versioned evidence','Every result, backed by evidence.','Trace a robot outcome or model response from its original image to a verified, versioned dataset.')
    st.info('Audit totals include all retained attempts. Physical scores use the dataset’s declared structured slots; model totals span its full captured corpus. Open Evaluations for fixed-benchmark results. Model proposals never operate the robot.')
    metrics=manifest['metrics']
    proposals=metrics.get('proposals',{})
    cols=st.columns(4)
    cols[0].metric('All robot attempts',str(metrics['catalog_episodes']),border=True)
    cols[1].metric('Physical task success',fraction(metrics['physical_task_success']),border=True)
    cols[2].metric('All model attempts',str(proposals.get('attempts',0)),border=True)
    cols[3].metric('Valid proposals',fraction(proposals.get('schema_valid_completed')),border=True)
    left,right=st.columns(2,gap='large')
    with left:
        with st.container(border=True):
            st.subheader('Structured execution')
            st.write('A bounded simulator controller executes known object and destination handles. Placement is measured independently.')
            st.metric('Normal task success',fraction(metrics['normal_task_success']))
            st.caption('The physical denominator includes declared injected faults.' if metrics['physical_task_success']['denominator']>metrics['normal_task_success']['denominator'] else 'Scores use the selected dataset’s declared structured slots. This case set has no injected-fault slots.')
            st.write('Expected rejection match: **'+fraction(metrics['rejection_match'])+'**')
    with right:
        with st.container(border=True):
            st.subheader('Experimental vision-language proposals')
            st.write('A pinned local model sees an image and an instruction. Original outputs, invalid JSON and timeouts are retained.')
            st.metric('Completed inference',fraction(proposals.get('provider_completion')))
            st.caption('Inference completion is not a correct plan. Physical success is not applicable to proposal-only runs.')
            st.write('Execution-eligible proposals: **'+fraction(proposals.get('execution_eligible'))+'** — enforced by policy')
    st.subheader('Evidence pipeline')
    st.write('Capture → preserve raw bytes → validate references → project PostgreSQL → freeze inputs → publish Parquet → evaluate')
    with st.expander('Dataset provenance and table inventory'):
        st.code(manifest['build_fingerprint'],language=None)
        st.caption('Build fingerprint · the selected dataset is immutable')
        st.dataframe([{'Table':name,'Rows':item['rows']} for name,item in manifest['tables'].items()],hide_index=True,width='stretch')
        st.caption('Each table and the frozen input are checksum-verified by the API before this manifest is returned.')


def evaluations(dataset_id):
    intro('Fixed inputs · explicit denominators','Evaluation workspace','Inspect the fixed benchmark separately from retrospective development evidence.')
    reports=[row for row in data('/api/v1/evaluations')['items'] if row['dataset_id']==dataset_id]
    if not reports:
        st.info('No evaluation report is registered for this dataset. Choose the current twelve-table dataset or run the evaluation command.')
        return
    by_id={row['report_id']:row for row in reports}
    selected=st.selectbox('Evaluation report',list(by_id),format_func=lambda key:by_id[key]['task_set_name']+' · '+by_id[key]['split']+' · '+key[:8])
    report=data('/api/v1/evaluations/'+selected)
    result=report['result']
    planned=report['split']=='planned_evaluation'
    covered=sum(m['coverage']['numerator'] for m in result['metrics'].values())
    expected_count=sum(m['coverage']['denominator'] for m in result['metrics'].values())
    if planned and covered==0:
        st.warning('PLANNED / NOT RUN — empty slots are shown as missing. These are not measured model failures or benchmark accuracy.')
    elif planned and covered<expected_count:
        st.warning('PARTIAL COVERAGE — '+str(covered)+' of '+str(expected_count)+' applicable configuration slots captured. This is not a completed benchmark.')
    elif planned:
        st.info('FIXED TASK SET — all applicable slots have assigned attempts. Inspect evidence quality and failure categories before interpreting scores.')
    else:
        st.info('RETROSPECTIVE DEVELOPMENT — labels describe previously captured smoke data. These cohorts are not a paired held-out comparison.')
    st.caption(result['task_set']['disclosure'])
    for column,mode,title in zip(st.columns(2,gap='large'),['BASELINE_STRUCTURED','VLM_EVALUATION'],['Structured baseline','Vision-language proposals']):
        with column:
            with st.container(border=True):
                m=result['metrics'][mode]
                st.subheader(title)
                st.metric('Captured / applicable slots',fraction(m['coverage']))
                st.write('Complete evidence: **'+fraction(m['complete_evidence'])+'**')
                if planned and m['coverage']['numerator']==0:
                    st.write('Result scoring: **Not measured — no assigned attempts**')
                else:
                    st.write('Expected result matches: **'+fraction(m['expectation_match'])+'**')
                    st.write(('Physical task success: **'+fraction(m['physical_success'])+'**') if mode=='BASELINE_STRUCTURED' else 'Physical task success: **Not applicable**')
                    if mode=='VLM_EVALUATION':
                        st.write('Schema-valid / applicable slots: **'+fraction(m['schema_valid'])+'**')
                st.caption(str(m['not_applicable'])+' slots are not applicable to this configuration.')
    st.subheader('Case-level accounting')
    mode=st.selectbox('Configuration',['All','BASELINE_STRUCTURED','VLM_EVALUATION'])
    failures=sorted({row['failure_code'] for row in result['rows']})
    failure=st.selectbox('Failure category',['All',*failures])
    rows=[row for row in result['rows'] if (mode=='All' or row['mode']==mode) and (failure=='All' or row['failure_code']==failure)]
    st.dataframe(rows,hide_index=True,width='stretch')
    st.download_button('Download this evaluation as JSON',json.dumps(report,indent=2),file_name='evaluation-'+report['report_id']+'.json',mime='application/json')
    with st.expander('Frozen report identities'):
        st.json({key:report[key] for key in ['report_id','dataset_id','task_set_sha256','assignment_sha256','fingerprint']})


def episodes():
    intro('Measured execution · linked artifacts','Robot episode explorer','Follow the camera frames, action trace and measured outcome of a structured robot attempt.')
    items=data('/api/v1/episodes?limit=100')['items']
    if not items:
        st.info('No robot episodes have been ingested.')
        return
    by_id={row['episode_id']:row for row in items}
    selected=st.selectbox('Robot episode',list(by_id),format_func=lambda key:str(by_id[key].get('outcome'))+' · '+key[:8])
    detail=data('/api/v1/episodes/'+selected)
    cols=st.columns(3)
    cols[0].metric('Outcome',detail.get('outcome') or 'Unknown')
    cols[1].metric('Evidence quality',detail['completeness_status'])
    cols[2].metric('Failure code',detail.get('failure_code') or 'None')
    events=data('/api/v1/episodes/'+selected+'/events?limit=500')['items']
    observations=[event for event in events if event['event_type']=='observation.captured']
    if observations:
        left,right=st.columns(2)
        for column,event,label in [(left,observations[0],'Initial observation'),(right,observations[-1],'Final observation')]:
            column.image(binary('/api/v1/artifacts/'+event['payload']['image_artifact_id']),caption=label,width='stretch')
        frame=st.selectbox('Inspect a captured frame',range(len(observations)),format_func=lambda index:'Frame '+str(index+1)+' · simulation '+str(observations[index]['simulation_time_ns']/1e9)+' s')
        with st.expander('Selected camera frame'):
            st.image(binary('/api/v1/artifacts/'+observations[frame]['payload']['image_artifact_id']),width=600)
    else:
        st.info('This attempt has no camera frames, as expected for a request rejected before execution.')
    trace,actions,evidence=st.tabs(['Event trace','Actions','Evidence & provenance'])
    with trace:
        st.dataframe([{'Sequence':e['producer_sequence'],'Event':e['event_type'],'Simulation ns':e['simulation_time_ns'],'Captured':e['captured_at_utc']} for e in events],hide_index=True,width='stretch')
    with actions:
        st.dataframe(detail['actions'],hide_index=True,width='stretch')
    with evidence:
        st.dataframe(detail['artifacts'],hide_index=True,width='stretch')
        st.json({'episode_id':selected,'content_fingerprint':detail.get('content_fingerprint'),'source_summary':detail.get('source_summary')})


def proposals():
    intro('Original outputs · no execution','Model proposal explorer','Inspect exactly what the model saw and returned, including malformed output and provider failures.')
    items=data('/api/v1/proposals?limit=100')['items']
    if not items:
        st.info('No model attempts have been ingested.')
        return
    by_id={row['proposal_id']:row for row in items}
    selected=st.selectbox('Model attempt',list(by_id),format_func=lambda key:by_id[key]['provider_status']+' · '+key[:8])
    detail=data('/api/v1/proposals/'+selected)
    roles={item['role']:item for item in detail['artifacts']}
    def content(role):
        return binary('/api/v1/proposal-artifacts/'+roles[role]['artifact_id'])
    left,right=st.columns([1,1.1],gap='large')
    with left:
        st.image(content('proposal_image'),caption='Original source observation · reused development image',width='stretch')
        st.caption('Source episode: '+detail['source_episode_id'])
    with right:
        st.subheader('Instruction')
        st.write(content('instruction').decode('utf-8'))
        st.metric('Provider status',detail['provider_status'])
        st.subheader('Original response')
        if 'model_response' in roles:
            st.code(content('model_response').decode('utf-8',errors='replace'),language=None)
            validation=detail['validation']
            if validation['schema_valid']:
                st.success('Schema-valid proposal; visual correctness is evaluated separately.')
            else:
                st.error('Invalid proposal schema. Original output retained without repair.')
        else:
            st.info('No completed response was produced. Schema validity is not assessed for this provider failure.')
        st.caption('Execution eligibility: FALSE · physical outcome: NOT_EXECUTED')
    with st.expander('Model provenance, validation and timings',expanded=True):
        metadata=detail['manifest']['provider_metadata']
        cols=st.columns(4)
        phases=metadata.get('worker',{}).get('timings_ms',{})
        for column,label,value in zip(cols,['Worker wall','Model load','Preprocessing','Generation'],[metadata.get('wall_ms'),phases.get('load'),phases.get('preprocess'),phases.get('generation')]):
            column.metric(label,f'{value/1000:.3f} s' if value is not None else 'Not recorded')
        st.caption('Worker wall includes startup, imports and model-file checks. Missing phase timings on timeout are not invented.')
        st.json({'model_id':detail['model_id'],'model_revision':detail['model_revision'],'configuration':detail['configuration'],'validation':detail['validation'],'runtime':metadata.get('worker',{})})


def quality(manifest):
    intro('Integrity · recovery · audit','Data quality and lineage','Check complete evidence separately from task outcomes or model correctness.')
    violations={**manifest['metrics']['quality_violations'],**{'proposal_'+key:value for key,value in manifest['metrics'].get('proposals',{}).get('quality_violations',{}).items()}}
    st.metric('Dataset quality assertions',str(sum(value==0 for value in violations.values()))+' / '+str(len(violations))+' passed')
    st.dataframe([{'Assertion':key,'Violations':value} for key,value in violations.items()],hide_index=True,width='stretch')
    for title,path in [('Robot ingestion findings','/api/v1/ingestion/findings'),('Proposal ingestion findings','/api/v1/proposal-ingestion/findings'),('Proposal receipts','/api/v1/proposal-ingestion/receipts'),('Pipeline run journal','/api/v1/pipeline-runs')]:
        with st.expander(title):
            items=data(path)['items']
            if items:
                st.dataframe(items,hide_index=True,width='stretch')
            else:
                st.success('No findings recorded for this captured data.')


def render():
    st.markdown('<style>.block-container{padding-top:4rem;max-width:1450px} [data-testid="stMetricValue"]{font-variant-numeric:tabular-nums} h1{letter-spacing:-.04em} [data-testid="stSidebar"]{border-right:1px solid #24334b}</style>',unsafe_allow_html=True)
    st.sidebar.markdown('## ◈ Evidence Lab')
    st.sidebar.caption('ROBOTICS · DATA · EVALUATION')
    page=st.sidebar.radio('Workspace',['Overview','Evaluations','Episodes','Proposals','Data quality'],label_visibility='collapsed')
    st.sidebar.divider()
    if st.sidebar.button('Refresh evidence',width='stretch'):
        st.cache_data.clear()
    try:
        versions=data('/api/v1/datasets')['items']
        if not versions:
            st.info('No dataset is published yet. Run ingestion and the dataset pipeline to begin.')
            return
        by_id={row['dataset_id']:row for row in versions}
        selected=st.sidebar.selectbox('Published dataset',list(by_id),format_func=lambda key:key[:8]+' · '+by_id[key]['created_at_utc'][:10])
        manifest=data('/api/v1/datasets/'+selected)
        st.sidebar.caption('Evidence views use the selected frozen dataset for summaries and evaluation. Explorers show live ingested records.')
        st.sidebar.caption('Read-only workspace · no model or robot controls')
        if page=='Overview': overview(manifest)
        elif page=='Evaluations': evaluations(selected)
        elif page=='Episodes': episodes()
        elif page=='Proposals': proposals()
        else: quality(manifest)
    except api.APIError as error:
        st.error(str(error))
        st.info('Check that the API is running and ready, then choose Refresh evidence.')


if __name__=='__main__':
    render()
