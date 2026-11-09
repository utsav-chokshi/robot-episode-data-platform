"""Step 6 local task/capture contracts, independent of robot libraries."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_input_version: Literal["1.0.0"] = "1.0.0"
    mode: Literal["BASELINE_STRUCTURED"] = "BASELINE_STRUCTURED"
    object_id: str = Field(min_length=1, max_length=64)
    destination_id: str = Field(min_length=1, max_length=64)
    seed: StrictInt = Field(default=7, ge=0, le=4294967295)
    scenario: Literal["normal", "no_grasp"] = "normal"


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"]
    event_id: UUID
    event_type: Literal["task.accepted", "task.rejected", "scene.reset", "observation.captured", "reference_state.captured", "action.started", "action.finished", "episode.finished"]
    episode_id: UUID
    producer_id: str
    producer_session_id: UUID
    producer_sequence: int = Field(ge=0)
    captured_at_utc: datetime
    simulation_time_ns: int | None = Field(ge=0)
    causation_event_id: UUID | None
    payload: dict


OBJECTS = {
    "package_01": {"position": [.45, -.15, .025], "rgba": [.9, .08, .08, 1]},
    "package_02": {"position": [.65, -.15, .025], "rgba": [.08, .8, .15, 1]},
    "package_03": {"position": [.65, 0, .025], "rgba": [.08, .2, .9, 1]},
}
DESTINATIONS = {"zone_a": [.45, .18, .025], "zone_b": [.65, .18, .025]}
ASSET_REVISION = "98a70c569c4c1b6da467a20012f766ee1f8dc521"


def task_reference_error(task: TaskInput) -> str | None:
    if task.object_id not in OBJECTS:
        return "OBJECT_UNAVAILABLE"
    if task.destination_id not in DESTINATIONS:
        return "DESTINATION_INVALID"
    return None


def evaluate_placement(center, half_extents_xy, destination, speed, lift_z):
    """Independent measured predicate; controller commands are not evidence."""
    import math
    values = [*center, *half_extents_xy, *destination, speed, lift_z]
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("Non-finite placement evidence")
    contained = all(abs(center[i] - destination[i]) + half_extents_xy[i] <= .065 for i in range(2))
    resting = abs(center[2] - .025) <= .008
    settled = speed < .02
    lifted = lift_z > .14
    return {
        "evaluator_version": "placement-v1",
        "object_center_xyz_m": list(center),
        "object_half_extents_xy_m": list(half_extents_xy),
        "destination_center_xyz_m": list(destination),
        "zone_half_width_m": .065,
        "linear_speed_m_s": speed,
        "lifted_center_z_m": lift_z,
        "contained_xy": contained,
        "resting_height": resting,
        "settled": settled,
        "lifted": lifted,
        "success": bool(contained and resting and settled and lifted),
    }
