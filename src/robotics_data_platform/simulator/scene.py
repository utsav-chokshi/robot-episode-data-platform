"""Panda scene and the verified numerical waypoint IK from the feasibility spike."""
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

from robotics_data_platform.simulator.contracts import OBJECTS, DESTINATIONS

HOME = np.array([0, -.4, 0, -2.0, 0, 1.6, .785398])
TARGET_ROT = np.diag([1., -1., -1.])
ASSET_ROOT = Path(os.environ.get("RDP_ROBOT_ASSET_ROOT", "/opt/robot-assets/franka_emika_panda"))


def create_scene():
    root = ET.parse(ASSET_ROOT / "panda.xml").getroot()
    root.find("compiler").set("meshdir", str(ASSET_ROOT / "assets"))
    root.remove(root.find("keyframe"))
    root.find("option").set("timestep", ".002")
    root.find("option").set("iterations", "100")
    ET.SubElement(ET.SubElement(root, "visual"), "global", offwidth="512", offheight="512")
    ET.SubElement(root.find(".//body[@name='hand']"), "site", name="grasp", pos="0 0 .1034", size=".003", rgba="0 0 0 0")
    world = root.find("worldbody")
    ET.SubElement(world,"light",pos=".3 -.3 2",dir="0 0 -1")
    ET.SubElement(world,"camera",name="overhead",pos=".55 .015 .85",quat="1 0 0 0",fovy="45")
    ET.SubElement(world,"geom",name="table",type="plane",size="1 1 .1",rgba=".32 .36 .4 1",friction="1 .01 .001")
    for name,pos in DESTINATIONS.items():
        ET.SubElement(world,"site",name=name,type="box",pos=f"{pos[0]} {pos[1]} .001",size=".065 .065 .001",rgba=".2 .6 .7 .3")
    for name,definition in OBJECTS.items():
        body = ET.SubElement(world,"body",name=name,pos=" ".join(map(str,definition["position"])))
        ET.SubElement(body,"freejoint",name=f"{name}_free")
        ET.SubElement(body,"geom",name=f"{name}_geom",type="box",size=".02 .02 .025",mass=".04",rgba=" ".join(map(str,definition["rgba"])),friction="1.5 .02 .002",condim="4")
    xml = ET.tostring(root, encoding="unicode")
    return mujoco.MjModel.from_xml_string(xml), xml


def inverse_kinematics(model, target, seed):
    probe = mujoco.MjData(model)
    probe.qpos[:7] = seed
    probe.qpos[7:9] = .04
    site = model.site("grasp").id
    jp, jr = np.zeros((3, model.nv)), np.zeros((3, model.nv))
    for _ in range(500):
        mujoco.mj_forward(model, probe)
        rot = probe.site_xmat[site].reshape(3,3)
        orientation_error = .5 * sum((np.cross(rot[:,i], TARGET_ROT[:,i]) for i in range(3)))
        error = np.concatenate([target - probe.site_xpos[site], orientation_error])
        if np.linalg.norm(error[:3]) < .0002 and np.linalg.norm(error[3:]) < .002:
            return probe.qpos[:7].copy()
        mujoco.mj_jacSite(model, probe, jp, jr, site)
        jacobian = np.vstack([jp[:,:7], jr[:,:7]])
        update = jacobian.T @ np.linalg.solve(jacobian @ jacobian.T + .001*np.eye(6), error)
        probe.qpos[:7] += np.clip(update, -.08, .08)
        probe.qpos[:7] = np.clip(probe.qpos[:7],model.jnt_range[:7,0]+.005,model.jnt_range[:7,1]-.005)
    raise RuntimeError(f"Waypoint IK did not converge for {target.tolist()}")
