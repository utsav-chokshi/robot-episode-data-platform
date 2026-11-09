"""Replaceable offline provider with a parent-enforced process deadline."""
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MODEL_ID = 'HuggingFaceTB/SmolVLM-256M-Instruct'
MODEL_REVISION = '7e3e67edbbed1bf9888184d9df282b700a323964'
PROFILE = {'provider':'smolvlm-local-v1','model_id':MODEL_ID,'model_revision':MODEL_REVISION,
           'device':'cpu','dtype':'float32','threads':2,'seed':7,'longest_edge':512,
           'image_splitting':False,'max_new_tokens':80,'do_sample':False,'trust_remote_code':False}


@dataclass(frozen=True)
class ProviderResult:
    status: str
    response: bytes | None
    metadata: dict
    diagnostic: bytes = b''


class Provider(Protocol):
    profile: dict
    def infer(self,request:Path,working:Path,timeout_seconds:float) -> ProviderResult: ...


def run_child(command,request,working,timeout_seconds):
    if not 0 < timeout_seconds <= 180:
        raise ValueError('Deadline must be positive and at most 180 seconds')
    started = time.perf_counter()
    timed_out = False
    with (working/'worker.log').open('wb') as log:
        try:
            process = subprocess.Popen([*command,str(request),str(working)],stdout=log,stderr=log,start_new_session=True)
        except OSError as error:
            return ProviderResult('MODEL_UNAVAILABLE',None,{'error_type':type(error).__name__,'wall_ms':(time.perf_counter()-started)*1000})
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    metadata = {'wall_ms':(time.perf_counter()-started)*1000,'return_code':process.returncode}
    progress = working/'progress.json'
    if progress.exists() and progress.stat().st_size <= 65536:
        try:
            metadata['worker'] = json.loads(progress.read_bytes())
        except ValueError:
            metadata['progress_invalid'] = True
    with (working/'worker.log').open('rb') as log:
        diagnostic = log.read(65536)
    if timed_out:
        return ProviderResult('MODEL_TIMEOUT',None,metadata,diagnostic)
    response = working/'response.bin'
    if process.returncode != 0 or not response.exists():
        status = 'MODEL_UNAVAILABLE' if metadata.get('worker',{}).get('error_type') in {'FileNotFoundError','LocalEntryNotFoundError','ImportError','ModuleNotFoundError'} else 'MODEL_ERROR'
        return ProviderResult(status,None,metadata,diagnostic)
    if response.stat().st_size > 1048576:
        return ProviderResult('RESPONSE_LIMIT_EXCEEDED',None,metadata,diagnostic)
    return ProviderResult('COMPLETED',response.read_bytes(),metadata,diagnostic)


class SmolProvider:
    profile = PROFILE

    def infer(self,request,working,timeout_seconds):
        return run_child(('/opt/vlm/bin/python','-m','robotics_data_platform.planning.smol_worker'),request,working,timeout_seconds)
