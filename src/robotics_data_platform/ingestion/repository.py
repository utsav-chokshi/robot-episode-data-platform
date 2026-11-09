"""Transactional metadata projection. This module has no execution interface."""
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import text

from robotics_data_platform.ingestion.contracts import DataIssue
from robotics_data_platform.ingestion.storage import canonical, sha


def uid(value):
    return UUID(value) if value else None


def receipt_row(connection,identity,receipt,status):
    connection.execute(text("INSERT INTO ingestion_receipts (receipt_sha256,source_episode_id,manifest_sha256,status) VALUES (:id,:episode,:manifest,:status) ON CONFLICT DO NOTHING"),{"id":identity,"episode":uid(receipt["episode_id"]),"manifest":receipt["manifest_sha256"],"status":status})


def project(engine,identity,receipt,bundle):
    summary = bundle["summary"]
    episode = uid(summary["episode_id"])
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(802701)"))
        for event in bundle["events"]:
            existing = connection.execute(text("SELECT sha256 FROM raw_events WHERE event_id=:id"),{"id":uid(event["event_id"])}).scalar_one_or_none()
            if existing is not None and existing != event["sha256"]:
                raise DataIssue("EVENT_ID_CONFLICT","Event identity already binds different bytes",event["event_id"])
            sequence = connection.execute(text("SELECT event_id FROM raw_events WHERE producer_session_id=:session AND producer_sequence=:seq"),{"session":uid(event["producer_session_id"]),"seq":event["producer_sequence"]}).scalar_one_or_none()
            if sequence is not None and str(sequence) != event["event_id"]:
                raise DataIssue("SEQUENCE_CONFLICT","Producer sequence already binds another event",event["producer_session_id"])
        for artifact in bundle["artifacts"]:
            existing = connection.execute(text("SELECT sha256,episode_id,byte_size,media_type,role,access_class,producer_event_id,created_at_utc FROM artifacts WHERE artifact_id=:id"),{"id":uid(artifact["artifact_id"])}).mappings().first()
            if existing is not None and (any(str(existing[field]) != str(artifact[field]) for field in existing if field != "created_at_utc") or existing["created_at_utc"] != datetime.fromisoformat(artifact["created_at_utc"].replace("Z","+00:00"))):
                raise DataIssue("ARTIFACT_ID_CONFLICT","Artifact identity already has another binding",artifact["artifact_id"])
        previous = connection.execute(text("SELECT content_fingerprint FROM episodes WHERE episode_id=:id"),{"id":episode}).first()
        if previous and previous[0] is not None:
            if previous[0] != bundle["fingerprint"]:
                raise DataIssue("EPISODE_ID_CONFLICT","Finalized episode already binds a different event/artifact set",str(episode))
            receipt_row(connection,identity,receipt,"DUPLICATE")
            connection.execute(text("UPDATE episodes SET ingestion_status='INGESTED',completeness_status='COMPLETE' WHERE episode_id=:id"),{"id":episode})
            connection.execute(text("UPDATE ingestion_findings SET resolved_by_receipt=:receipt WHERE episode_id=:episode AND resolved_by_receipt IS NULL AND code NOT IN ('EVENT_ID_CONFLICT','SEQUENCE_CONFLICT','ARTIFACT_ID_CONFLICT','EPISODE_ID_CONFLICT','REFERENCE_ID_CONFLICT')"),{"receipt":identity,"episode":episode})
            return "DUPLICATE"
        connection.execute(text("""INSERT INTO episodes (episode_id,task_id,mode,execution_status,outcome,failure_code,ingestion_status,completeness_status,content_fingerprint,source_summary)
            VALUES (:episode,:task,:mode,:execution,:outcome,:failure,'INGESTED','COMPLETE',:fingerprint,CAST(:summary AS jsonb))
            ON CONFLICT (episode_id) DO UPDATE SET task_id=excluded.task_id,mode=excluded.mode,execution_status=excluded.execution_status,outcome=excluded.outcome,failure_code=excluded.failure_code,ingestion_status='INGESTED',completeness_status='COMPLETE',content_fingerprint=excluded.content_fingerprint,source_summary=excluded.source_summary"""),
            {"episode":episode,"task":uid(summary["task_id"]),"mode":summary["mode"],"execution":summary["execution_status"],"outcome":summary["outcome"],"failure":summary["failure_code"],"fingerprint":bundle["fingerprint"],"summary":json.dumps(summary)})
        for event in bundle["events"]:
            connection.execute(text("""INSERT INTO raw_events (event_id,episode_id,producer_id,producer_session_id,producer_sequence,event_type,sha256,payload,captured_at_utc,simulation_time_ns)
                VALUES (:id,:episode,:producer,:session,:seq,:type,:hash,CAST(:payload AS jsonb),:captured,:sim)"""),
                {"id":uid(event["event_id"]),"episode":episode,"producer":event["producer_id"],"session":uid(event["producer_session_id"]),"seq":event["producer_sequence"],"type":event["event_type"],"hash":event["sha256"],"payload":json.dumps(event["payload"]),"captured":event["captured_at_utc"],"sim":event["simulation_time_ns"]})
        for artifact in bundle["artifacts"]:
            connection.execute(text("""INSERT INTO artifacts (artifact_id,episode_id,producer_event_id,sha256,byte_size,role,media_type,access_class,integrity_state,created_at_utc)
                VALUES (:id,:episode,:event,:hash,:size,:role,:media,:access,'VERIFIED',:created)"""),
                {"id":uid(artifact["artifact_id"]),"episode":episode,"event":uid(artifact["producer_event_id"]),"hash":artifact["sha256"],"size":artifact["byte_size"],"role":artifact["role"],"media":artifact["media_type"],"access":artifact["access_class"],"created":artifact["created_at_utc"]})
        for observation in bundle["observations"]:
            payload = observation["payload"]
            connection.execute(text("""INSERT INTO observations (observation_id,episode_id,event_id,image_artifact_id,camera_config_artifact_id,camera_id,frame_id,width_px,height_px)
                VALUES (:id,:episode,:event,:image,:config,:camera,:frame,:width,:height)"""),
                {"id":uid(payload["observation_id"]),"episode":episode,"event":uid(observation["event_id"]),"image":uid(payload["image_artifact_id"]),"config":uid(payload["camera_config_artifact_id"]),"camera":payload["camera_id"],"frame":payload["frame_id"],"width":payload["width_px"],"height":payload["height_px"]})
        for action_id,start in bundle["starts"].items():
            finish = bundle["finishes"][action_id]
            a,f = start["payload"],finish["payload"]
            connection.execute(text("""INSERT INTO actions (action_id,episode_id,command_id,primitive,object_id,destination_id,started_event_id,finished_event_id,measurement_artifact_id,status,duration_ms,failure_code)
                VALUES (:id,:episode,:command,:primitive,:object,:destination,:start,:finish,:measurement,:status,:duration,:failure)"""),
                {"id":uid(action_id),"episode":episode,"command":uid(a["command_id"]),"primitive":a["primitive"],"object":a["object_id"],"destination":a["destination_id"],"start":uid(start["event_id"]),"finish":uid(finish["event_id"]),"measurement":uid(f["measurement_artifact_id"]),"status":f["status"],"duration":f["duration_ms"],"failure":f["failure_code"]})
        terminal = bundle["terminal"]
        connection.execute(text("INSERT INTO outcomes (episode_id,event_id,outcome,failure_code,measurement_artifact_id) VALUES (:episode,:event,:outcome,:failure,:measurement)"),{"episode":episode,"event":uid(terminal["event_id"]),"outcome":summary["outcome"],"failure":summary["failure_code"],"measurement":uid(terminal["payload"]["measurement_artifact_id"])})
        receipt_row(connection,identity,receipt,"INGESTED")
        connection.execute(text("UPDATE ingestion_findings SET resolved_by_receipt=:receipt WHERE episode_id=:episode AND resolved_by_receipt IS NULL AND code NOT IN ('EVENT_ID_CONFLICT','SEQUENCE_CONFLICT','ARTIFACT_ID_CONFLICT','EPISODE_ID_CONFLICT','REFERENCE_ID_CONFLICT')"),{"receipt":identity,"episode":episode})
    return "INGESTED"


def record_issue(engine,identity,receipt,issue,status):
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(802701)"))
        receipt_row(connection,identity,receipt,status)
        episode = uid(receipt["episode_id"])
        connection.execute(text("""INSERT INTO episodes (episode_id,mode,execution_status,ingestion_status,completeness_status)
            VALUES (:episode,'UNKNOWN','UNKNOWN',:status,:quality) ON CONFLICT DO NOTHING"""),{"episode":episode,"status":status,"quality":"CORRUPT" if "CORRUPT" in issue.code or "MISMATCH" in issue.code else "INCOMPLETE"})
        connection.execute(text("UPDATE episodes SET ingestion_status=:status,completeness_status=:quality WHERE episode_id=:episode AND content_fingerprint IS NULL"),{"episode":episode,"status":status,"quality":"CORRUPT" if "CORRUPT" in issue.code or "MISMATCH" in issue.code else "INCOMPLETE"})
        if issue.code in {"RAW_OBJECT_CORRUPT","RAW_OBJECT_MISSING"}:
            connection.execute(text("UPDATE episodes SET completeness_status='CORRUPT' WHERE episode_id=:id"),{"id":episode})
        finding_id = sha(canonical([identity,issue.code,issue.reference]))
        connection.execute(text("""INSERT INTO ingestion_findings (finding_id,receipt_sha256,episode_id,code,message,reference)
            VALUES (:id,:receipt,:episode,:code,:message,:reference) ON CONFLICT DO NOTHING"""),{"id":finding_id,"receipt":identity,"episode":episode,"code":issue.code,"message":str(issue)[:512],"reference":str(issue.reference)[:256] if issue.reference is not None else None})


def counts(engine):
    with engine.connect() as connection:
        return {name:connection.scalar(text(f"SELECT count(*) FROM {name}")) for name in ["episodes","raw_events","artifacts","observations","actions","outcomes","ingestion_receipts","ingestion_findings"]}
