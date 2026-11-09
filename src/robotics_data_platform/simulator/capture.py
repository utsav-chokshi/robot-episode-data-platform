"""Append-only local episode capture. Ingestion and recovery belong to Step 7."""
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from robotics_data_platform.simulator.contracts import Event


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".partial")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class EpisodeCapture:
    def __init__(self, root: Path):
        self.episode_id, self.task_id, self.session_id = map(str, [uuid4(), uuid4(), uuid4()])
        self.path = root / "episodes" / self.episode_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.sequence = 0
        self.events, self.artifacts = [], []
        self.previous = None
        self.active_action = None
        self.command_id = None
        self.completed = False
        atomic_write(self.path / "attempt.json", canonical({"episode_id":self.episode_id, "mode":"BASELINE_STRUCTURED", "started_at_utc":utc_now(), "capture_state":"OPEN"}))

    def artifact(self, data: bytes, role: str, media_type="application/json", access_class="CONFIGURATION"):
        digest = hashlib.sha256(data).hexdigest()
        relative = f"objects/sha256/{digest[:2]}/{digest}"
        path = self.path / relative
        if path.exists():
            if path.read_bytes() != data:
                raise RuntimeError("Content-addressed artifact conflict")
        else:
            atomic_write(path, data)
        identity = str(uuid4())
        self.artifacts.append({"artifact_id":identity, "episode_id":self.episode_id, "role":role, "media_type":media_type, "access_class":access_class, "byte_size":len(data), "sha256":digest, "relative_path":relative, "created_at_utc":utc_now(), "producer_event_id":None, "integrity_state":"PENDING"})
        return identity

    def json_artifact(self, data, role, access_class="CONFIGURATION"):
        return self.artifact(canonical(data), role, access_class=access_class)

    def event(self, event_type, payload, simulation_time_s=None):
        identity = str(uuid4())
        event = Event(
            schema_version="1.0.0", event_id=identity, event_type=event_type,
            episode_id=self.episode_id, producer_id="simulator-cli-01",
            producer_session_id=self.session_id, producer_sequence=self.sequence,
            captured_at_utc=utc_now(), simulation_time_ns=None if simulation_time_s is None else round(simulation_time_s * 1e9),
            causation_event_id=self.previous, payload=payload,
        ).model_dump(mode="json")
        data = canonical(event)
        relative = f"events/{self.sequence:06d}-{identity}.json"
        atomic_write(self.path / relative, data)
        for artifact in self.artifacts:
            if artifact["producer_event_id"] is None:
                artifact["producer_event_id"] = identity
        self.events.append({"event_id":identity,"producer_sequence":self.sequence,"relative_path":relative,"sha256":hashlib.sha256(data).hexdigest(),"byte_size":len(data)})
        self.sequence += 1
        self.previous = identity
        return identity

    def action_start(self, primitive, task, sim_time):
        self.active_action = str(uuid4())
        self.event("action.started", {"action_id":self.active_action,"command_id":self.command_id,"primitive":primitive,"object_id":task.object_id,"destination_id":task.destination_id if primitive == "PLACE" else None}, sim_time)

    def action_finish(self, status, measurements, duration_ms, sim_time, failure_code=None):
        artifact_id = self.json_artifact(measurements, "action_measurements", "EVALUATION_REFERENCE")
        self.event("action.finished", {"action_id":self.active_action,"status":status,"measurement_artifact_id":artifact_id,"duration_ms":duration_ms,"failure_code":failure_code}, sim_time)
        self.active_action = None
        return artifact_id

    def finish(self, outcome, failure_code=None, measurement_artifact_id=None, sim_time=None):
        if self.completed:
            raise RuntimeError("Episode is already terminal")
        self.event("episode.finished", {"outcome":outcome,"failure_code":failure_code,"measurement_artifact_id":measurement_artifact_id,"expected_last_sequences":{self.session_id:self.sequence}}, sim_time)
        summary = {"episode_id":self.episode_id,"task_id":self.task_id,"mode":"BASELINE_STRUCTURED","command_id":self.command_id,"execution_status":"INTERRUPTED" if outcome == "INTERRUPTED" else "FINISHED","outcome":outcome,"failure_code":failure_code,"capture_state":"READY","ingestion_status":"PENDING","completeness_status":"OPEN","quality_assessed":False,"event_count":len(self.events),"artifact_count":len(self.artifacts),"finished_at_utc":utc_now()}
        atomic_write(self.path / "episode.json", canonical(summary))
        # Publishing the manifest is the local bundle's final marker.
        atomic_write(self.path / "manifest.json", canonical({"bundle_contract_version":"1.0.0","episode":summary,"events":self.events,"artifacts":self.artifacts}))
        self.completed = True
        return summary


def managed_episode(root: Path, episode_id: str):
    validated = str(UUID(episode_id))
    return root / "episodes" / validated


def verify_episode(path: Path):
    manifest = json.loads((path / "manifest.json").read_bytes())
    for entry in manifest["events"] + manifest["artifacts"]:
        file = (path / entry["relative_path"]).resolve()
        if not file.is_relative_to(path.resolve()):
            raise ValueError("Artifact path escapes episode")
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["byte_size"]:
            raise ValueError("Captured content integrity mismatch")
    events = [Event.model_validate_json((path / e["relative_path"]).read_bytes()) for e in manifest["events"]]
    if [e.producer_sequence for e in events] != list(range(len(events))):
        raise ValueError("Event sequence gap")
    if not events or events[-1].event_type != "episode.finished":
        raise ValueError("Terminal evidence missing")
    if any(str(e.episode_id) != manifest["episode"]["episode_id"] for e in events):
        raise ValueError("Cross-episode event reference")
    if events[-1].payload["expected_last_sequences"] != {str(events[-1].producer_session_id):len(events)-1}:
        raise ValueError("Producer stream closure mismatch")
    return manifest["episode"]


def terminal_measurements(root: Path, episode_id: str):
    path = managed_episode(root,episode_id)
    manifest = json.loads((path/"manifest.json").read_bytes())
    terminal = json.loads((path/manifest["events"][-1]["relative_path"]).read_bytes())
    identity = terminal["payload"]["measurement_artifact_id"]
    if identity is None:
        return None
    entry = next(a for a in manifest["artifacts"] if a["artifact_id"] == identity)
    return json.loads((path/entry["relative_path"]).read_bytes())


def export_episode(root: Path, episode_id: str, destination: Path):
    source = managed_episode(root, episode_id)
    verify_episode(source)
    target = destination / str(UUID(episode_id))
    # Refuse to overwrite an earlier export.
    shutil.copytree(source, target)
    for artifact in json.loads((source / "manifest.json").read_bytes())["artifacts"]:
        if artifact["media_type"] == "image/png":
            shutil.copyfile(source / artifact["relative_path"], target / (artifact["role"] + ".png"))
    return target
