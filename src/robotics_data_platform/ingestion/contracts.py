"""Strict receiver contracts for completed Step 6 capture bundles."""
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, UUID4, field_validator

Reason = Literal["OBJECT_UNAVAILABLE","OBJECT_AMBIGUOUS","DESTINATION_INVALID","PLAN_SCHEMA_INVALID","MODEL_TIMEOUT","MODEL_UNAVAILABLE","GRASP_FAILED","PLACEMENT_FAILED","EXECUTION_INTERRUPTED","CONTROLLER_ERROR"]
Outcome = Literal["SUCCEEDED","FAILED","REJECTED","CLARIFICATION_REQUIRED","NOT_EXECUTED","INTERRUPTED"]


class DataIssue(ValueError):
    def __init__(self, code, message, reference=None, pending=False):
        self.code, self.reference, self.pending = code, reference, pending
        super().__init__(message)


def load_json(data):
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("Non-finite JSON number")
    return json.loads(data,object_pairs_hook=pairs,parse_constant=invalid_constant)


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Envelope(StrictRecord):
    schema_version: Literal["1.0.0"]
    event_id: UUID4
    event_type: str = Field(min_length=1,max_length=64)
    episode_id: UUID4
    producer_id: str = Field(min_length=1,max_length=128)
    producer_session_id: UUID4
    producer_sequence: StrictInt = Field(ge=0,le=9223372036854775807)
    captured_at_utc: AwareDatetime
    simulation_time_ns: StrictInt | None = Field(ge=0,le=9223372036854775807)
    causation_event_id: UUID4 | None
    payload: dict

    @field_validator("captured_at_utc")
    @classmethod
    def utc_only(cls,value):
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("UTC timestamp required")
        return value


class Accepted(StrictRecord):
    task_id: UUID4
    mode: Literal["BASELINE_STRUCTURED"]
    request_artifact_id: UUID4
    scene_config_artifact_id: UUID4
    command_id: UUID4


class Rejected(StrictRecord):
    task_id: UUID4
    request_artifact_id: UUID4
    failure_code: Reason


class Reset(StrictRecord):
    seed: StrictInt = Field(ge=0,le=4294967295)
    scene_config_artifact_id: UUID4
    controller_revision: str = Field(pattern="^[0-9a-f]{64}$")
    asset_revision: str = Field(pattern="^[0-9a-f]{40}$")
    simulation_timestep_ns: StrictInt = Field(gt=0)
    frame_id: str = Field(min_length=1,max_length=128)


class Observation(StrictRecord):
    observation_id: UUID4
    camera_id: str = Field(min_length=1,max_length=128)
    image_artifact_id: UUID4
    camera_config_artifact_id: UUID4
    frame_id: str = Field(min_length=1,max_length=128)
    width_px: StrictInt = Field(gt=0,le=4096)
    height_px: StrictInt = Field(gt=0,le=4096)


class Reference(StrictRecord):
    state_artifact_id: UUID4
    frame_id: str = Field(min_length=1,max_length=128)
    purpose: Literal["evaluation_reference"]


class ActionStart(StrictRecord):
    action_id: UUID4
    command_id: UUID4
    primitive: Literal["PICK","PLACE"]
    object_id: str = Field(min_length=1,max_length=64)
    destination_id: str | None = Field(max_length=64)


class ActionFinish(StrictRecord):
    action_id: UUID4
    status: Literal["SUCCEEDED","FAILED"]
    measurement_artifact_id: UUID4
    duration_ms: float = Field(ge=0)
    failure_code: Reason | None


class Finished(StrictRecord):
    outcome: Outcome
    failure_code: Reason | None
    measurement_artifact_id: UUID4 | None
    expected_last_sequences: dict[UUID4,StrictInt]


PAYLOADS = {"task.accepted":Accepted,"task.rejected":Rejected,"scene.reset":Reset,"observation.captured":Observation,"reference_state.captured":Reference,"action.started":ActionStart,"action.finished":ActionFinish,"episode.finished":Finished}


class EventEntry(StrictRecord):
    event_id: UUID4
    producer_sequence: StrictInt = Field(ge=0)
    relative_path: str = Field(min_length=1,max_length=240)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    byte_size: StrictInt = Field(ge=0,le=262144)


class ArtifactEntry(StrictRecord):
    artifact_id: UUID4
    episode_id: UUID4
    role: str = Field(min_length=1,max_length=128)
    media_type: Literal["application/json","application/xml","image/png"]
    access_class: Literal["OBSERVATION","MODEL_IO","EVALUATION_REFERENCE","CONFIGURATION"]
    byte_size: StrictInt = Field(ge=0,le=8388608)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    relative_path: str = Field(min_length=1,max_length=240)
    created_at_utc: AwareDatetime
    producer_event_id: UUID4
    integrity_state: Literal["PENDING"]


class EpisodeSummary(StrictRecord):
    episode_id: UUID4
    task_id: UUID4
    mode: Literal["BASELINE_STRUCTURED"]
    command_id: UUID4 | None
    execution_status: Literal["FINISHED","INTERRUPTED"]
    outcome: Outcome
    failure_code: Reason | None
    capture_state: Literal["READY"]
    ingestion_status: Literal["PENDING"]
    completeness_status: Literal["OPEN"]
    quality_assessed: Literal[False]
    event_count: StrictInt = Field(ge=1,le=1000)
    artifact_count: StrictInt = Field(ge=0,le=1000)
    finished_at_utc: AwareDatetime


class Manifest(StrictRecord):
    bundle_contract_version: Literal["1.0.0"]
    episode: EpisodeSummary
    events: list[EventEntry] = Field(min_length=1,max_length=1000)
    artifacts: list[ArtifactEntry] = Field(max_length=1000)
