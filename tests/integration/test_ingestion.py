import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text
from sqlalchemy.exc import IntegrityError

from robotics_data_platform.api.factory import create_app
from robotics_data_platform.ingestion.repository import counts
from robotics_data_platform.ingestion.storage import canonical
from robotics_data_platform.ingestion.worker import Receiver
from robotics_data_platform.settings import Settings
from robotics_data_platform.simulator.capture import EpisodeCapture
from robotics_data_platform.simulator.runner import execute_request
from ingestion_helpers import manifest,mutate_event,success_bundle,write_manifest

pytestmark = pytest.mark.integration


@pytest.fixture
def receiver(fresh_database,tmp_path):
    command.upgrade(Config("alembic.ini"),"head")
    engine = create_engine(fresh_database)
    value = Receiver(engine,tmp_path/"raw")
    yield value
    engine.dispose()


def test_ingestion_repeated_three_times_and_raw_only_replay(receiver,tmp_path,monkeypatch,fresh_database):
    source = success_bundle(tmp_path/"capture")
    assert receiver.ingest(source)["status"] == "INGESTED"
    baseline = counts(receiver.engine)
    assert receiver.ingest(source)["status"] == "DUPLICATE"
    assert receiver.ingest(source)["status"] == "DUPLICATE"
    assert counts(receiver.engine) == baseline
    shutil.move(str(source.parent),str(tmp_path/"unavailable-capture"))
    monkeypatch.setattr("robotics_data_platform.simulator.runner.execute_request",lambda *a,**k:pytest.fail("Replay must never execute a robot task"))
    assert receiver.replay()[0]["status"] == "DUPLICATE"
    assert counts(receiver.engine) == baseline
    with TestClient(create_app(Settings(database_url=fresh_database,raw_root=receiver.store.root))) as client:
        result = client.get("/api/v1/episodes").json()["items"][0]
        assert result["completeness_status"] == "COMPLETE"
        detail = client.get("/api/v1/episodes/"+result["episode_id"]).json()
        image = next(a for a in detail["artifacts"] if a["media_type"] == "image/png")
        assert client.get("/api/v1/artifacts/"+image["artifact_id"]).content.startswith(b"\x89PNG")


def test_database_commit_and_receipt_crash_boundaries(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    def crash(stage):
        if stage == "raw_persisted":
            raise RuntimeError("injected crash")
    receiver.checkpoint = crash
    with pytest.raises(RuntimeError,match="injected"):
        receiver.ingest(source)
    assert counts(receiver.engine)["raw_events"] == 0
    receiver.checkpoint = lambda stage:None
    assert receiver.replay()[0]["status"] == "INGESTED"
    second = success_bundle(tmp_path/"capture")
    receiver.checkpoint = lambda stage: (_ for _ in ()).throw(RuntimeError("after commit")) if stage == "db_committed" else None
    with pytest.raises(RuntimeError,match="after commit"):
        receiver.ingest(second)
    committed = counts(receiver.engine)
    receiver.checkpoint = lambda stage:None
    assert all(r["status"] == "DUPLICATE" for r in receiver.replay())
    assert counts(receiver.engine) == committed


def test_database_outage_retains_raw_receipt_for_recovery(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    original = receiver.engine
    unavailable = create_engine("postgresql+psycopg://test:fake@127.0.0.1:1/missing",connect_args={"connect_timeout":1})
    receiver.engine = unavailable
    assert receiver.ingest(source)["status"] == "PENDING_DB"
    assert list((receiver.store.root/"receipts").glob("*.json"))
    receiver.engine = original
    unavailable.dispose()
    assert receiver.replay()[0]["status"] == "INGESTED"


def test_changed_event_identity_does_not_overwrite_accepted_history(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    receiver.ingest(source)
    before = counts(receiver.engine)
    mutate_event(source,0,lambda e:e.update(captured_at_utc="2026-11-09T00:00:00Z"))
    result = receiver.ingest(source)
    assert result["status"] == "CONFLICTED" and result["code"] == "EVENT_ID_CONFLICT"
    assert counts(receiver.engine)["raw_events"] == before["raw_events"]
    assert counts(receiver.engine)["outcomes"] == before["outcomes"]


def test_producer_sequence_conflict_is_retained(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    receiver.ingest(source)
    value = manifest(source)
    old_id,new_id = value["events"][0]["event_id"],str(uuid4())
    mutate_event(source,0,lambda e:e.update(event_id=new_id))
    mutate_event(source,1,lambda e:e.update(causation_event_id=new_id))
    value = manifest(source)
    for artifact in value["artifacts"]:
        if artifact["producer_event_id"] == old_id:
            artifact["producer_event_id"] = new_id
    write_manifest(source,value)
    result = receiver.ingest(source)
    assert result["status"] == "CONFLICTED" and result["code"] == "SEQUENCE_CONFLICT"


@pytest.mark.parametrize("fault,code",[("missing","ARTIFACT_MISSING"),("corrupt","ARTIFACT_HASH_MISMATCH"),("bad_image","IMAGE_INVALID"),("version","UNSUPPORTED_SCHEMA"),("payload","CONTRACT_INVALID"),("traversal","PATH_INVALID")])
def test_bad_input_is_quarantined_without_partial_projection(receiver,tmp_path,fault,code):
    source = success_bundle(tmp_path/"capture")
    value = manifest(source)
    image = next(a for a in value["artifacts"] if a["media_type"] == "image/png")
    if fault == "missing":
        (source/image["relative_path"]).unlink()
    elif fault == "corrupt":
        (source/image["relative_path"]).write_bytes(b"corrupt")
    elif fault == "bad_image":
        import hashlib
        data = b"not a PNG image"
        digest = hashlib.sha256(data).hexdigest()
        relative = f"objects/sha256/{digest[:2]}/{digest}"
        (source/relative).parent.mkdir(parents=True,exist_ok=True)
        (source/relative).write_bytes(data)
        image.update(sha256=digest,byte_size=len(data),relative_path=relative)
        write_manifest(source,value)
    elif fault == "version":
        mutate_event(source,0,lambda e:e.update(schema_version="9.0.0"))
    elif fault == "payload":
        mutate_event(source,0,lambda e:e["payload"].update(unexpected=True))
    else:
        value["artifacts"][0]["relative_path"] = "../outside.json"
        write_manifest(source,value)
    result = receiver.ingest(source)
    assert result["status"] == "QUARANTINED" and result["code"] == code
    assert counts(receiver.engine)["raw_events"] == 0
    assert counts(receiver.engine)["ingestion_findings"] == 1
    receiver.replay()
    assert counts(receiver.engine)["ingestion_findings"] == 1


def test_reference_arrival_resolves_pending_episode(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    original = manifest(source)
    modified = json.loads(json.dumps(original))
    modified["artifacts"] = [a for a in modified["artifacts"] if a["media_type"] != "image/png"]
    modified["episode"]["artifact_count"] = len(modified["artifacts"])
    write_manifest(source,modified)
    assert receiver.ingest(source)["status"] == "PENDING_REFERENCES"
    write_manifest(source,original)
    assert receiver.ingest(source)["status"] == "INGESTED"
    with receiver.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM ingestion_findings WHERE resolved_by_receipt IS NULL")) == 0


def test_ordering_and_cross_episode_foreign_keys(receiver,tmp_path):
    first = success_bundle(tmp_path/"capture")
    value = manifest(first)
    value["events"].reverse()
    write_manifest(first,value)
    assert receiver.ingest(first)["status"] == "INGESTED"
    second = success_bundle(tmp_path/"capture")
    assert receiver.ingest(second)["status"] == "INGESTED"
    with pytest.raises(IntegrityError):
        with receiver.engine.begin() as connection:
            connection.execute(text("UPDATE observations SET image_artifact_id=(SELECT artifact_id FROM artifacts WHERE episode_id != observations.episode_id AND media_type='image/png' LIMIT 1)"))


def test_stale_partial_is_visible_and_reconcile_never_dispatches(receiver,tmp_path):
    capture = EpisodeCapture(tmp_path/"capture")
    capture.json_artifact({"object_id":"package_01"},"task_request")
    assert receiver.scan(tmp_path/"capture") == []
    result = receiver.scan(tmp_path/"capture",stale_after_seconds=0)[0]
    assert result["status"] == "PENDING_REFERENCES"
    with receiver.engine.connect() as connection:
        row = connection.execute(text("SELECT outcome,execution_status,completeness_status FROM episodes")).one()
        assert row == (None,"UNKNOWN","INCOMPLETE")


def test_raw_object_audit_detects_post_ingestion_corruption(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    receiver.ingest(source)
    image = next(a for a in manifest(source)["artifacts"] if a["media_type"] == "image/png")
    receiver.store.object_path(image["sha256"]).write_bytes(b"corrupted raw object")
    assert receiver.replay()[0]["code"] == "RAW_OBJECT_CORRUPT"
    with receiver.engine.connect() as connection:
        assert connection.scalar(text("SELECT completeness_status FROM episodes")) == "CORRUPT"
    receiver.store.object_path(image["sha256"]).write_bytes((source/image["relative_path"]).read_bytes())
    assert receiver.replay()[0]["status"] == "DUPLICATE"
    with receiver.engine.connect() as connection:
        assert connection.scalar(text("SELECT completeness_status FROM episodes")) == "COMPLETE"
        assert connection.scalar(text("SELECT count(*) FROM ingestion_findings WHERE resolved_by_receipt IS NULL")) == 0


def test_projection_rolls_back_when_a_late_relationship_insert_conflicts(receiver,tmp_path):
    first = success_bundle(tmp_path/"capture")
    receiver.ingest(first)
    value = manifest(first)
    first_observation = next(json.loads((first/e["relative_path"]).read_bytes())["payload"]["observation_id"] for e in value["events"] if json.loads((first/e["relative_path"]).read_bytes())["event_type"] == "observation.captured")
    second = success_bundle(tmp_path/"capture")
    mutate_event(second,2,lambda e:e["payload"].update(observation_id=first_observation))
    before = counts(receiver.engine)
    result = receiver.ingest(second)
    assert result["status"] == "CONFLICTED" and result["code"] == "REFERENCE_ID_CONFLICT"
    after = counts(receiver.engine)
    for table in ["raw_events","artifacts","observations","actions","outcomes"]:
        assert before[table] == after[table]


def test_concurrent_delivery_has_one_projection(receiver,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    source = success_bundle(tmp_path/"capture")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(receiver.ingest,[source,source]))
    assert sorted(r["status"] for r in results) == ["DUPLICATE","INGESTED"]
    assert counts(receiver.engine)["episodes"] == 1
    assert counts(receiver.engine)["outcomes"] == 1


def test_oversized_manifest_is_rejected_with_retained_source(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    file = source/"manifest.json"
    original = file.read_bytes()
    file.write_bytes(original + b" "*1048576)
    result = receiver.ingest(source)
    assert result["status"] == "QUARANTINED" and result["code"] == "CAPTURE_LIMIT_EXCEEDED"
    assert file.exists() and file.stat().st_size > 1048576
    assert counts(receiver.engine)["raw_events"] == 0


def test_artifact_identity_cannot_change_its_creation_time(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    receiver.ingest(source)
    value = manifest(source)
    value["artifacts"][0]["created_at_utc"] = "2026-11-08T00:00:00Z"
    write_manifest(source,value)
    result = receiver.ingest(source)
    assert result["status"] == "CONFLICTED" and result["code"] == "ARTIFACT_ID_CONFLICT"


def test_low_raw_storage_stops_admission_without_reclassifying_source(receiver,tmp_path):
    source = success_bundle(tmp_path/"capture")
    receiver.store.min_free_bytes = 10**20
    with pytest.raises(OSError,match="low-space"):
        receiver.ingest(source)
    assert (source/"manifest.json").exists()
    assert counts(receiver.engine)["raw_events"] == 0
