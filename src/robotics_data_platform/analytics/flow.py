"""Prefect isolated from the API's dependency environment."""
import json
import os
import subprocess
from prefect import flow,task
from prefect.cache_policies import NO_CACHE


@task(name='Freeze accepted evidence',persist_result=False,cache_policy=NO_CACHE)
def freeze():
    return invoke('freeze','--definition',os.environ.get('RDP_DATASET_DEFINITION_PATH','/app/examples/simulator-suite.json'),'--assignment',os.environ.get('RDP_CAPTURE_ASSIGNMENT_PATH','/capture/suite-result.json'))['input_sha256']


@task(name='Publish versioned dataset',retries=1,retry_delay_seconds=2,persist_result=False,cache_policy=NO_CACHE)
def build(identity):
    return invoke('build','--identity',identity)


def invoke(*arguments):
    from prefect.runtime import flow_run
    environment = {**os.environ,'RDP_PREFECT_RUN_ID':str(flow_run.id)}
    process = subprocess.run(['/usr/local/bin/python','-m','robotics_data_platform.analytics.cli',*arguments],capture_output=True,text=True,env=environment)
    if process.returncode:
        # Generic task failure; application journal keeps the bounded exception class.
        raise RuntimeError('Dataset command failed; inspect the application pipeline run journal')
    return json.loads(process.stdout)


@flow(name='Robotics development dataset',persist_result=False,log_prints=False)
def dataset_flow():
    return build(freeze())


if __name__ == '__main__':
    print(json.dumps(dataset_flow(),sort_keys=True))
