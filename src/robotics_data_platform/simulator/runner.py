"""Explicit single-attempt entry point; no task queue, retry or model execution."""
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

from pydantic import ValidationError

from robotics_data_platform.simulator.capture import EpisodeCapture, canonical
from robotics_data_platform.simulator.contracts import TaskInput, task_reference_error


def execute_request(request: bytes | dict, capture_root: Path):
    capture = EpisodeCapture(capture_root)
    raw = request if isinstance(request,bytes) else canonical(request)
    request_id = capture.artifact(raw,"task_request","application/json")
    try:
        task = TaskInput.model_validate_json(raw)
        error = task_reference_error(task)
    except ValidationError:
        task, error = None, "PLAN_SCHEMA_INVALID"
    if error:
        capture.event("task.rejected",{"task_id":capture.task_id,"request_artifact_id":request_id,"failure_code":error})
        return capture.finish("REJECTED",error)
    try:
        from robotics_data_platform.simulator.controller import execute
        module_root = Path(__file__).parent
        source_hashes = {name:hashlib.sha256((module_root/name).read_bytes()).hexdigest() for name in ["controller.py","scene.py","contracts.py","capture.py","runner.py"]}
        provenance = {"controller_source_sha256":source_hashes["controller.py"],"source_files_sha256":source_hashes,"python":platform.python_version(),"machine":platform.machine(),"dependencies":{name:importlib.metadata.version(name) for name in ["mujoco","numpy","pillow","pydantic"]},"execution_source":"explicit_structured_cli_task","model_used":False}
        return execute(task,capture,request_id,provenance)
    except KeyboardInterrupt:
        if capture.active_action:
            capture.action_finish("FAILED",{"reason":"operator/process interruption"},0,None,"EXECUTION_INTERRUPTED")
        capture.finish("INTERRUPTED","EXECUTION_INTERRUPTED")
        raise
    except Exception as error:
        if capture.completed:
            raise
        diagnostic = {"error_type":type(error).__name__,"message":str(error)}
        if capture.active_action:
            measurement = capture.action_finish("FAILED",diagnostic,0,None,"CONTROLLER_ERROR")
        else:
            measurement = capture.json_artifact(diagnostic,"controller_error","EVALUATION_REFERENCE")
        return capture.finish("FAILED","CONTROLLER_ERROR",measurement)
