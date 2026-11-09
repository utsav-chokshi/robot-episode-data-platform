"""Synthetic contract fixtures; these are not robot-performance measurements."""
import io
import json
from uuid import uuid4

from PIL import Image

from robotics_data_platform.simulator.capture import EpisodeCapture,canonical
from robotics_data_platform.simulator.contracts import evaluate_placement


def success_bundle(root):
    capture = EpisodeCapture(root)
    capture.command_id = str(uuid4())
    request = capture.json_artifact({"object_id":"package_01","destination_id":"zone_a","seed":7},"task_request")
    scene = capture.json_artifact({"objects":{"package_01":{}},"destinations":{"zone_a":{}},"seed":7},"scene_config")
    camera = capture.json_artifact({"image_width_px":1,"image_height_px":1,"frame_id":"world","scene_config_artifact_id":scene},"camera_config")
    capture.event("task.accepted",{"task_id":capture.task_id,"mode":"BASELINE_STRUCTURED","request_artifact_id":request,"scene_config_artifact_id":scene,"command_id":capture.command_id})
    capture.event("scene.reset",{"seed":7,"scene_config_artifact_id":scene,"controller_revision":"0"*64,"asset_revision":"1"*40,"simulation_timestep_ns":2000000,"frame_id":"world"},0)
    stream = io.BytesIO()
    Image.new("RGB",(1,1),(255,0,0)).save(stream,format="PNG")
    image = capture.artifact(stream.getvalue(),"initial","image/png","OBSERVATION")
    capture.event("observation.captured",{"observation_id":str(uuid4()),"camera_id":"test-camera","image_artifact_id":image,"camera_config_artifact_id":camera,"frame_id":"world","width_px":1,"height_px":1},.8)
    state = capture.json_artifact({"frame_id":"world","object_xyz_m":[.45,-.15,.025]},"initial_reference_state","EVALUATION_REFERENCE")
    capture.event("reference_state.captured",{"state_artifact_id":state,"frame_id":"world","purpose":"evaluation_reference"},.8)
    from robotics_data_platform.simulator.contracts import TaskInput
    task = TaskInput(object_id="package_01",destination_id="zone_a")
    capture.action_start("PICK",task,1.)
    capture.action_finish("SUCCEEDED",{"lifted_center_z_m":.22},100,2.)
    capture.action_start("PLACE",task,2.)
    measurement = evaluate_placement([.45,.18,.025],[.02,.02],[.45,.18,.025],0,.22)
    identity = capture.action_finish("SUCCEEDED",measurement,100,3.)
    capture.finish("SUCCEEDED",measurement_artifact_id=identity,sim_time=3.)
    return capture.path


def manifest(path):
    return json.loads((path/"manifest.json").read_bytes())


def write_manifest(path,value):
    (path/"manifest.json").write_bytes(canonical(value))


def mutate_event(path,index,change):
    from hashlib import sha256
    value = manifest(path)
    entry = value["events"][index]
    file = path/entry["relative_path"]
    event = json.loads(file.read_bytes())
    change(event)
    data = canonical(event)
    file.write_bytes(data)
    entry.update(sha256=sha256(data).hexdigest(),byte_size=len(data),event_id=event["event_id"],producer_sequence=event["producer_sequence"])
    write_manifest(path,value)
