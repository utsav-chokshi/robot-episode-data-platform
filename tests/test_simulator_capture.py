import json

import pytest

from robotics_data_platform.simulator.capture import managed_episode, verify_episode
from robotics_data_platform.simulator.contracts import evaluate_placement
from robotics_data_platform.simulator.runner import execute_request


def test_experimental_request_cannot_create_a_command(tmp_path):
    result = execute_request({"mode":"VLM_EVALUATION","object_id":"package_01","destination_id":"zone_a"},tmp_path)
    assert result["outcome"] == "REJECTED"
    assert result["failure_code"] == "PLAN_SCHEMA_INVALID"
    assert result["command_id"] is None
    assert result["event_count"] == 2
    verify_episode(managed_episode(tmp_path,result["episode_id"]))


@pytest.mark.parametrize("task_request,code",[
    ({"object_id":"missing","destination_id":"zone_a"},"OBJECT_UNAVAILABLE"),
    ({"object_id":"package_01","destination_id":"missing"},"DESTINATION_INVALID"),
])
def test_rejections_are_preserved_without_fictional_actions(tmp_path,task_request,code):
    result = execute_request(task_request,tmp_path)
    path = managed_episode(tmp_path,result["episode_id"])
    assert result["outcome"] == "REJECTED" and result["failure_code"] == code
    assert result["command_id"] is None
    manifest = json.loads((path/"manifest.json").read_bytes())
    events = [json.loads((path/e["relative_path"]).read_bytes()) for e in manifest["events"]]
    assert [e["event_type"] for e in events] == ["task.rejected","episode.finished"]
    assert all(a["media_type"] != "image/png" for a in manifest["artifacts"])


def test_capture_integrity_detects_changed_bytes(tmp_path):
    result = execute_request({"object_id":"missing","destination_id":"zone_a"},tmp_path)
    path = managed_episode(tmp_path,result["episode_id"])
    verify_episode(path)
    manifest = json.loads((path/"manifest.json").read_bytes())
    artifact = path/manifest["artifacts"][0]["relative_path"]
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError,match="integrity"):
        verify_episode(path)


def test_placement_requires_whole_footprint_and_settled_object():
    result = evaluate_placement([.50,.18,.025],[.02,.02],[.45,.18,.025],0,.22)
    assert result["contained_xy"] is False
    assert result["success"] is False
    moving = evaluate_placement([.45,.18,.025],[.02,.02],[.45,.18,.025],.05,.22)
    assert moving["success"] is False


def test_nonfinite_measurements_are_not_success():
    with pytest.raises(ValueError,match="Non-finite"):
        evaluate_placement([float("nan"),.18,.025],[.02,.02],[.45,.18,.025],0,.22)
