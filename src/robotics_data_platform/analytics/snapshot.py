"""Freeze a repeatable catalog view, raw evidence assessment and expected slots."""
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from robotics_data_platform.analytics.files import digest,immutable_json,normalized
from robotics_data_platform.ingestion.contracts import DataIssue
from robotics_data_platform.ingestion.storage import RawStore
from robotics_data_platform.ingestion.validation import validate_receipt


def expected_slots(definition,assignment,split='development'):
    cases = definition["cases"]
    identifiers = [case["case_id"] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate expected case ID")
    assigned = {}
    for row in assignment.get("results",[]):
        key = row["case_id"]
        if key not in identifiers or key in assigned:
            raise ValueError("Unexpected/duplicate assignment case")
        case = next(c for c in cases if c["case_id"] == key)
        if row["request"] != case["request"] or row["expected_outcome"] != case["expected_outcome"] or row.get("expected_failure_code") != case.get("expected_failure_code"):
            raise ValueError("Assignment labels differ from predeclared cases")
        assigned[key] = row.get("actual",{}).get("episode_id")
    values = [value for value in assigned.values() if value]
    if len(values) != len(set(values)):
        raise ValueError("One attempt cannot fill two expected slots")
    return [{"case_id":case["case_id"],"request":case["request"],"expected_outcome":case["expected_outcome"],"expected_failure_code":case.get("expected_failure_code"),"episode_id":assigned.get(case["case_id"]),"category":"rejection" if case["expected_outcome"] == "REJECTED" else "injected_fault" if case["expected_outcome"] == "FAILED" else "normal","split":split} for case in cases]


def freeze_inputs(engine,raw_root:Path,dataset_root:Path,definition,assignment,*,task_set_id='step6-development-v1',split='development'):
    slots = expected_slots(definition,assignment,split)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            catalog = normalized([dict(r) for r in connection.execute(text("SELECT * FROM episodes ORDER BY episode_id")).mappings()])
            receipts = normalized([dict(r) for r in connection.execute(text("SELECT * FROM ingestion_receipts WHERE status IN ('INGESTED','DUPLICATE') ORDER BY processed_at_utc,receipt_sha256")).mappings()])
            findings = normalized([dict(r) for r in connection.execute(text("SELECT * FROM ingestion_findings ORDER BY finding_id")).mappings()])
            proposal_catalog=normalized([dict(r) for r in connection.execute(text('SELECT * FROM plan_attempts ORDER BY proposal_id')).mappings()])
            proposal_receipts=normalized([dict(r) for r in connection.execute(text('SELECT * FROM proposal_receipts ORDER BY receipt_sha256')).mappings()])
            proposal_findings=normalized([dict(r) for r in connection.execute(text('SELECT * FROM proposal_findings ORDER BY finding_id')).mappings()])
    store = RawStore(raw_root)
    members = []
    for episode in catalog:
        bundle,receipt,issues = None,None,[]
        candidates = [r for r in receipts if r["source_episode_id"] == episode["episode_id"]]
        if episode["content_fingerprint"] is None:
            issues.append({"code":"CAPTURE_INCOMPLETE","message":"No accepted finalized snapshot","reference":None})
        else:
            for candidate in candidates:
                try:
                    receipt = store.read_receipt(raw_root/"receipts"/(candidate["receipt_sha256"]+".json"))
                    parsed = validate_receipt(store,receipt)
                    if parsed["fingerprint"] != episode["content_fingerprint"]:
                        continue
                    bundle = parsed
                    break
                except (DataIssue,ValueError,OSError) as error:
                    issues.append({"code":getattr(error,"code","RAW_RECEIPT_INVALID"),"message":str(error)[:256],"reference":getattr(error,"reference",None)})
            if bundle is None and not issues:
                issues.append({"code":"RAW_RECEIPT_MISSING","message":"No verified receipt for accepted snapshot","reference":None})
        if bundle is not None:
            issues = []
            summary = bundle["summary"]
            if any(episode[k] != summary[k] for k in ["mode","execution_status","outcome","failure_code"]):
                issues.append({"code":"PROJECTION_MISMATCH","message":"Catalog outcome differs from immutable source","reference":None})
            if episode["completeness_status"] != "COMPLETE":
                issues.append({"code":"CATALOG_NOT_COMPLETE","message":"Receiver assessment is not complete","reference":None})
        eligible = bundle is not None and not issues
        quality = "COMPLETE" if eligible else "CORRUPT" if episode["content_fingerprint"] else "INCOMPLETE"
        assessment = {"episode_id":episode["episode_id"],"source_fingerprint":episode["content_fingerprint"],"quality":quality,"eligible":eligible,"issues":issues,"event_hashes":sorted((e["event_id"],e["sha256"]) for e in bundle["events"]) if eligible else [],"artifact_hashes":sorted((a["artifact_id"],a["sha256"]) for a in bundle["artifacts"]) if eligible else []}
        seal_digest = digest(assessment)
        immutable_json(dataset_root/"seals"/(seal_digest+".json"),assessment)
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(802708)"))
            seal = connection.execute(text("SELECT seal_id,revision FROM episode_seals WHERE episode_id=:episode AND seal_digest=:digest"),{"episode":episode["episode_id"],"digest":seal_digest}).mappings().first()
            if seal is None:
                revision = connection.scalar(text("SELECT COALESCE(max(revision),0)+1 FROM episode_seals WHERE episode_id=:episode"),{"episode":episode["episode_id"]})
                seal = {"seal_id":str(uuid4()),"revision":revision}
                connection.execute(text("INSERT INTO episode_seals (seal_id,episode_id,revision,seal_digest,quality,eligible,assessment) VALUES (:id,:episode,:revision,:digest,:quality,:eligible,CAST(:body AS jsonb))"),{"id":seal["seal_id"],"episode":episode["episode_id"],"revision":revision,"digest":seal_digest,"quality":quality,"eligible":eligible,"body":json.dumps(assessment)})
        auxiliary = {}
        if eligible:
            for artifact in bundle["artifacts"]:
                if artifact["role"] in {"task_request","scene_config"}:
                    try:
                        auxiliary[artifact["role"]] = json.loads(store.get(artifact["sha256"]))
                    except (ValueError,UnicodeError):
                        auxiliary[artifact["role"]] = None
        members.append(normalized({"catalog":episode,"seal":{**dict(seal),"digest":seal_digest,**assessment},"bundle":bundle if eligible else None,"receipt":receipt if eligible else None,"auxiliary":auxiliary}))
    value = {"input_contract_version":"1.0.0","task_set":{"id":task_set_id,"split":split,"definition_sha256":digest(definition),"slots":slots},"members":members,"receiver_findings":findings}
    if proposal_receipts:
        from robotics_data_platform.analytics.proposal_snapshot import assess_proposals
        value['input_contract_version']='1.1.0'
        value['proposals']=assess_proposals(raw_root,proposal_catalog,proposal_receipts,proposal_findings)
    identity = digest(value)
    immutable_json(dataset_root/"inputs"/(identity+".json"),value)
    return identity


def verify_frozen_evidence(snapshot,raw_root):
    from robotics_data_platform.analytics.proposal_snapshot import verify_proposals
    verify_proposals(snapshot,raw_root)
    store = RawStore(raw_root)
    for member in snapshot["members"]:
        if member["seal"]["eligible"]:
            current = validate_receipt(store,member["receipt"])
            if current["fingerprint"] != member["bundle"]["fingerprint"]:
                raise ValueError("Frozen source fingerprint changed")
