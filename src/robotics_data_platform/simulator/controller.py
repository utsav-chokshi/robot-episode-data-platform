"""Bounded pick/place sequence; no object welds or runtime pose resets."""
import io
import hashlib
import time
from uuid import uuid4

import mujoco
import numpy as np
from PIL import Image

from robotics_data_platform.simulator.contracts import OBJECTS, DESTINATIONS, ASSET_REVISION, evaluate_placement
from robotics_data_platform.simulator.scene import HOME, ASSET_ROOT, create_scene, inverse_kinematics


def execute(task, capture, request_artifact, provenance):
    model, xml = create_scene()
    provenance["asset_panda_xml_sha256"] = hashlib.sha256((ASSET_ROOT/"panda.xml").read_bytes()).hexdigest()
    scene_xml = capture.artifact(xml.encode(), "scene_xml", "application/xml")
    config = capture.json_artifact({"scene_id":"tabletop-v1","scene_xml_artifact_id":scene_xml,"objects":OBJECTS,"destinations":DESTINATIONS,"object_half_size_m":[.02,.02,.025],"seed":task.seed,"scenario":task.scenario,"reference_controller_uses_simulator_ground_truth":True,"provenance":provenance},"scene_config")
    camera_config = capture.json_artifact({"camera_id":"overhead-01","frame_id":"world","position_m":[.55,.015,.85],"quaternion_wxyz":[1,0,0,0],"fovy_degrees":45,"image_width_px":512,"image_height_px":512,"renderer":"osmesa","pixel_origin":"top-left","extrinsics_source":"MuJoCo scene, camera -Z viewing axis and +Y up","scene_config_artifact_id":config},"camera_config")
    capture.command_id = str(uuid4())
    capture.event("task.accepted", {"task_id":capture.task_id,"mode":"BASELINE_STRUCTURED","request_artifact_id":request_artifact,"scene_config_artifact_id":config,"command_id":capture.command_id})
    data = mujoco.MjData(model)
    start = np.array(OBJECTS[task.object_id]["position"], dtype=float)
    start[:2] += np.random.default_rng(task.seed).uniform(-.012,.012,2)
    destination = np.array(DESTINATIONS[task.destination_id], dtype=float)
    data.qpos[:7], data.qpos[7:9] = HOME, .04
    address = model.jnt_qposadr[model.joint(task.object_id + "_free").id]
    data.qpos[address:address+3] = start
    data.ctrl[:7], data.ctrl[7] = HOME, 255
    mujoco.mj_forward(model,data)
    capture.event("scene.reset", {"seed":task.seed,"scene_config_artifact_id":config,"controller_revision":provenance["controller_source_sha256"],"asset_revision":ASSET_REVISION,"simulation_timestep_ns":2000000,"frame_id":"world"},data.time)
    target_id = model.body(task.object_id).id
    target_geom = model.geom(task.object_id + "_geom").id
    grasp_id = model.site("grasp").id
    dof = model.jnt_dofadr[model.joint(task.object_id + "_free").id]

    def tick():
        data.qfrc_applied[:7] = data.qfrc_bias[:7]
        mujoco.mj_step(model,data)

    def hold(seconds):
        for _ in range(round(seconds/model.opt.timestep)):
            tick()

    def move(point, seconds=2):
        initial = data.ctrl[:7].copy()
        goal = inverse_kinematics(model, point, initial)
        steps = round(seconds/model.opt.timestep)
        for index in range(steps):
            fraction = (index+1)/steps
            blend = fraction*fraction*(3-2*fraction)
            data.ctrl[:7] = initial + blend*(goal-initial)
            tick()
        hold(.4)

    with mujoco.Renderer(model,height=512,width=512) as renderer:
        def snapshot(phase):
            renderer.update_scene(data,camera="overhead")
            output = io.BytesIO()
            Image.fromarray(renderer.render()).save(output,format="PNG")
            image_id = capture.artifact(output.getvalue(),phase,"image/png","OBSERVATION")
            capture.event("observation.captured", {"observation_id":str(uuid4()),"camera_id":"overhead-01","image_artifact_id":image_id,"camera_config_artifact_id":camera_config,"frame_id":"world","width_px":512,"height_px":512},data.time)
            state_id = capture.json_artifact({"phase":phase,"frame_id":"world","simulation_time_ns":round(data.time*1e9),"object_id":task.object_id,"object_xyz_m":data.xpos[target_id].tolist(),"object_quaternion_wxyz":data.xquat[target_id].tolist(),"grasp_xyz_m":data.site_xpos[grasp_id].tolist(),"robot_joint_positions_rad":data.qpos[:7].tolist()},phase+"_reference_state","EVALUATION_REFERENCE")
            capture.event("reference_state.captured",{"state_artifact_id":state_id,"frame_id":"world","purpose":"evaluation_reference"},data.time)

        hold(.8)
        snapshot("initial")
        action_started = time.perf_counter()
        capture.action_start("PICK",task,data.time)
        move(np.array([start[0],start[1],.20]))
        move(start+[0,0,.002],1.5)
        snapshot("pregrasp")
        for index in range(750):
            data.ctrl[7] = 255 if task.scenario == "no_grasp" else 255*max(0.,1-(index+1)/400)
            tick()
        contact_bodies = set()
        for index in range(data.ncon):
            contact = data.contact[index]
            names = [model.body(int(model.geom_bodyid[g])).name for g in [contact.geom1,contact.geom2]]
            if task.object_id in names:
                contact_bodies.update(name for name in names if name in {"left_finger","right_finger"})
        snapshot("closed")
        move(np.array([start[0],start[1],.22]))
        snapshot("lifted")
        lift_z = float(data.xpos[target_id,2])
        picked = lift_z > .14
        pick_measurement = capture.action_finish("SUCCEEDED" if picked else "FAILED",{"object_id":task.object_id,"lifted_center_z_m":lift_z,"lift_threshold_m":.14,"contact_bodies":sorted(contact_bodies),"scenario":task.scenario},(time.perf_counter()-action_started)*1000,data.time,None if picked else "GRASP_FAILED")
        if not picked:
            return capture.finish("FAILED","GRASP_FAILED",pick_measurement,data.time)
        action_started = time.perf_counter()
        capture.action_start("PLACE",task,data.time)
        move(np.array([destination[0],destination[1],.22]),2.5)
        move(destination+[0,0,.012],1.5)
        data.ctrl[7] = 255
        hold(1.)
        move(np.array([destination[0],destination[1],.25]))
        hold(1.)
        snapshot("final")
        rotation = data.geom_xmat[target_geom].reshape(3,3)
        extents = np.abs(rotation) @ np.array([.02,.02,.025])
        measurements = evaluate_placement(data.xpos[target_id].tolist(),extents[:2].tolist(),destination.tolist(),float(np.linalg.norm(data.qvel[dof:dof+3])),lift_z)
        success = measurements["success"]
        measurement_id = capture.action_finish("SUCCEEDED" if success else "FAILED",measurements,(time.perf_counter()-action_started)*1000,data.time,None if success else "PLACEMENT_FAILED")
        return capture.finish("SUCCEEDED" if success else "FAILED",None if success else "PLACEMENT_FAILED",measurement_id,data.time)
