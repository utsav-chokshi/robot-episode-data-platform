"""Validate bytes, typed payloads, references and episode closure before projection."""
import io
from collections import defaultdict

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from robotics_data_platform.ingestion.contracts import DataIssue, Envelope, Manifest, PAYLOADS, load_json
from robotics_data_platform.ingestion.storage import canonical


def require(condition,code,message,reference=None,pending=False):
    if not condition:
        raise DataIssue(code,message,reference,pending)


def validate_receipt(store,receipt):
    if receipt["source_issues"]:
        issue = receipt["source_issues"][0]
        raise DataIssue(issue["code"],issue["message"],issue["reference"],receipt["partial_capture"])
    raw_manifest = store.get(receipt["manifest_sha256"])
    raw = load_json(raw_manifest)
    require(raw.get("bundle_contract_version") == "1.0.0","UNSUPPORTED_SCHEMA","Unsupported bundle version")
    try:
        manifest = Manifest.model_validate_json(raw_manifest)
    except ValidationError as error:
        raise DataIssue("CONTRACT_INVALID","Manifest contract validation failed") from error
    summary = manifest.episode.model_dump(mode="json")
    episode_id = str(manifest.episode.episode_id)
    require(episode_id == receipt["episode_id"],"EPISODE_ID_MISMATCH","Capture folder and episode identity differ")
    require(len(manifest.events) == summary["event_count"] and len(manifest.artifacts) == summary["artifact_count"],"COUNT_MISMATCH","Manifest counts disagree")
    artifact_ids = [str(a.artifact_id) for a in manifest.artifacts]
    require(len(artifact_ids) == len(set(artifact_ids)),"ARTIFACT_ID_CONFLICT","Duplicate artifact identity in bundle")
    artifacts = {str(a.artifact_id):a.model_dump(mode="json") for a in manifest.artifacts}
    events,blobs,json_values = [],{},{}
    for entry in [*manifest.events,*manifest.artifacts]:
        data = entry.model_dump(mode="json")
        actual = receipt["files"].get(entry.relative_path)
        require(actual is not None,"ARTIFACT_MISSING","Referenced bytes missing",entry.relative_path)
        require(actual["sha256"] == entry.sha256 and actual["byte_size"] == entry.byte_size,"ARTIFACT_HASH_MISMATCH","Declared content hash/size differs from received bytes",entry.relative_path)
        blob = store.get(actual["sha256"])
        if "artifact_id" in data:
            identity = str(entry.artifact_id)
            require(str(entry.episode_id) == episode_id,"REFERENCE_CROSS_EPISODE","Artifact belongs to another episode",identity)
            require(entry.relative_path == f"objects/sha256/{entry.sha256[:2]}/{entry.sha256}","PATH_INVALID","Artifact path differs from its content address",identity)
            blobs[identity] = blob
            if entry.media_type == "application/json" and entry.role != "task_request":
                try:
                    json_values[identity] = load_json(blob)
                except (ValueError,UnicodeError) as error:
                    raise DataIssue("ARTIFACT_FORMAT_INVALID","JSON artifact is malformed",identity) from error
            if entry.media_type == "image/png":
                try:
                    with Image.open(io.BytesIO(blob)) as image:
                        require(image.format == "PNG" and image.width*image.height <= 4194304,"IMAGE_INVALID","Unexpected image format or pixel count",identity)
                        image.load()
                        data["decoded_dimensions"] = [image.width,image.height]
                except (OSError,ValueError,Image.DecompressionBombError,UnidentifiedImageError) as error:
                    if isinstance(error,DataIssue):
                        raise
                    raise DataIssue("IMAGE_INVALID","Image cannot be decoded",identity) from error
                artifacts[identity] = data
        else:
            require(entry.relative_path.startswith("events/"),"PATH_INVALID","Event outside events namespace")
            raw_event = load_json(blob)
            require(raw_event.get("schema_version") == "1.0.0","UNSUPPORTED_SCHEMA","Unsupported event version",str(entry.event_id))
            require(blob == canonical(raw_event),"EVENT_NOT_CANONICAL","Event bytes are not canonical",str(entry.event_id))
            try:
                event = Envelope.model_validate_json(blob)
                require(event.event_type in PAYLOADS,"UNSUPPORTED_SCHEMA","Unsupported event type",event.event_type)
                payload = PAYLOADS[event.event_type].model_validate_json(canonical(event.payload)).model_dump(mode="json")
            except ValidationError as error:
                raise DataIssue("CONTRACT_INVALID","Event envelope or payload validation failed",str(entry.event_id)) from error
            require(str(event.event_id) == str(entry.event_id) and event.producer_sequence == entry.producer_sequence,"EVENT_ID_MISMATCH","Event entry differs from its bytes")
            require(str(event.episode_id) == episode_id,"REFERENCE_CROSS_EPISODE","Event belongs to another episode")
            value = event.model_dump(mode="json")
            value.update(payload=payload,sha256=entry.sha256,byte_size=entry.byte_size)
            events.append(value)
    event_ids = {e["event_id"] for e in events}
    require(len(event_ids) == len(events),"EVENT_ID_CONFLICT","Duplicate event identity in bundle")
    streams = defaultdict(list)
    by_event = {e["event_id"]:e for e in events}
    for event in events:
        streams[event["producer_session_id"]].append(event)
        cause = event["causation_event_id"]
        require(cause is None or cause in by_event,"REFERENCE_UNRESOLVED","Causation event missing",cause,True)
        if cause and by_event[cause]["producer_session_id"] == event["producer_session_id"]:
            require(by_event[cause]["producer_sequence"] < event["producer_sequence"],"EVENT_ORDER_INVALID","Invalid causal order")
    for identity,stream in streams.items():
        stream.sort(key=lambda e:e["producer_sequence"])
        require([e["producer_sequence"] for e in stream] == list(range(len(stream))),"REFERENCE_UNRESOLVED","Producer stream gap",identity,True)
        times = [e["simulation_time_ns"] for e in stream if e["simulation_time_ns"] is not None]
        require(times == sorted(times),"EVENT_ORDER_INVALID","Simulation time moved backwards",identity)
    terminal = [e for e in events if e["event_type"] == "episode.finished"]
    require(len(terminal) == 1,"REFERENCE_UNRESOLVED","One terminal event required",pending=True)
    terminal = terminal[0]
    require(terminal["producer_sequence"] == len(streams[terminal["producer_session_id"]])-1,"EVENT_ORDER_INVALID","Terminal event must close its stream")
    require(terminal["payload"]["expected_last_sequences"] == {identity:len(stream)-1 for identity,stream in streams.items()},"REFERENCE_UNRESOLVED","Terminal stream closure mismatch",pending=True)
    for field in ["outcome","failure_code"]:
        require(terminal["payload"][field] == summary[field],"OUTCOME_MISMATCH","Summary and terminal outcome differ")
    require((summary["execution_status"] == "INTERRUPTED") == (summary["outcome"] == "INTERRUPTED"),"OUTCOME_MISMATCH","Interrupted lifecycle mismatch")
    require(summary["outcome"] != "SUCCEEDED" or summary["failure_code"] is None,"OUTCOME_MISMATCH","Success cannot carry a failure code")
    require(summary["outcome"] not in {"FAILED","REJECTED","INTERRUPTED"} or summary["failure_code"] is not None,"OUTCOME_MISMATCH","Failed/rejected/interrupted result needs a reason")

    def artifact(identity,access=None,media=None):
        require(identity in artifacts,"REFERENCE_UNRESOLVED","Artifact reference missing",identity,True)
        item = artifacts[identity]
        require(item["producer_event_id"] in event_ids,"REFERENCE_UNRESOLVED","Artifact source event missing",identity,True)
        require(access is None or item["access_class"] == access,"ACCESS_CLASS_INVALID","Artifact role/access mismatch",identity)
        require(media is None or item["media_type"] == media,"ARTIFACT_FORMAT_INVALID","Artifact media type mismatch",identity)
        return item

    for identity in artifacts:
        artifact(identity)
    intents = [e for e in events if e["event_type"] in {"task.accepted","task.rejected"}]
    require(len(intents) == 1,"REFERENCE_UNRESOLVED","One task intent required",pending=True)
    intent = intents[0]
    require(intent["payload"]["task_id"] == summary["task_id"],"REFERENCE_CROSS_EPISODE","Task identity differs")
    artifact(intent["payload"]["request_artifact_id"],"CONFIGURATION","application/json")
    observations,starts,finishes = [],{},{}
    for event in events:
        payload = event["payload"]
        if event["event_type"] == "observation.captured":
            image = artifact(payload["image_artifact_id"],"OBSERVATION","image/png")
            require(image["decoded_dimensions"] == [payload["width_px"],payload["height_px"]],"IMAGE_DIMENSION_MISMATCH","Image dimensions disagree")
            artifact(payload["camera_config_artifact_id"],"CONFIGURATION","application/json")
            camera = json_values[payload["camera_config_artifact_id"]]
            require(camera.get("image_width_px") == payload["width_px"] and camera.get("image_height_px") == payload["height_px"] and camera.get("frame_id") == payload["frame_id"],"CAMERA_CONFIG_MISMATCH","Camera configuration differs from observation")
            require(payload["observation_id"] not in {o["payload"]["observation_id"] for o in observations},"OBSERVATION_ID_CONFLICT","Duplicate observation")
            observations.append(event)
        elif event["event_type"] == "reference_state.captured":
            artifact(payload["state_artifact_id"],"EVALUATION_REFERENCE","application/json")
        elif event["event_type"] == "action.started":
            require(payload["action_id"] not in starts,"ACTION_ID_CONFLICT","Duplicate action start")
            starts[payload["action_id"]] = event
        elif event["event_type"] == "action.finished":
            require(payload["action_id"] not in finishes,"ACTION_ID_CONFLICT","Duplicate action finish")
            artifact(payload["measurement_artifact_id"],"EVALUATION_REFERENCE","application/json")
            require((payload["status"] == "SUCCEEDED") == (payload["failure_code"] is None),"OUTCOME_MISMATCH","Action failure code mismatch")
            finishes[payload["action_id"]] = event
    require(set(starts) == set(finishes),"REFERENCE_UNRESOLVED","Unfinished or unstarted action",pending=True)
    measurement = terminal["payload"]["measurement_artifact_id"]
    if measurement:
        artifact(measurement,"EVALUATION_REFERENCE","application/json")
    if intent["event_type"] == "task.rejected":
        require(not starts and summary["command_id"] is None and summary["outcome"] == "REJECTED","OUTCOME_MISMATCH","Rejected input cannot have commands/actions")
        require(intent["payload"]["failure_code"] == summary["failure_code"],"OUTCOME_MISMATCH","Rejection reasons differ")
    else:
        require(intent["payload"]["command_id"] == summary["command_id"],"OUTCOME_MISMATCH","Command identity mismatch")
        scene_id = intent["payload"]["scene_config_artifact_id"]
        artifact(scene_id,"CONFIGURATION","application/json")
        configuration = json_values[scene_id]
        resets = [e for e in events if e["event_type"] == "scene.reset"]
        require(len(resets) == 1 and observations,"REFERENCE_UNRESOLVED","Reset and observation required",pending=True)
        require(resets[0]["payload"]["scene_config_artifact_id"] == scene_id,"REFERENCE_CROSS_EPISODE","Reset configuration differs")
        for action_id,start in starts.items():
            payload = start["payload"]
            require(payload["command_id"] == summary["command_id"],"REFERENCE_CROSS_EPISODE","Action command differs")
            require(payload["object_id"] in configuration.get("objects",{}),"REFERENCE_UNRESOLVED","Action object missing from configuration",pending=True)
            if payload["primitive"] == "PLACE":
                require(payload["destination_id"] in configuration.get("destinations",{}),"REFERENCE_UNRESOLVED","Action destination missing",pending=True)
            else:
                require(payload["destination_id"] is None,"CONTRACT_INVALID","PICK cannot specify placement destination")
            finish = finishes[action_id]
            require(start["producer_session_id"] == finish["producer_session_id"] and start["producer_sequence"] < finish["producer_sequence"],"EVENT_ORDER_INVALID","Action finish precedes start")
        if summary["outcome"] == "SUCCEEDED":
            require({s["payload"]["primitive"] for s in starts.values()} == {"PICK","PLACE"} and all(f["payload"]["status"] == "SUCCEEDED" for f in finishes.values()) and measurement is not None,"OUTCOME_MISMATCH","Successful episode lacks successful measured actions")
    immutable_artifacts = [{k:a[k] for k in ["artifact_id","episode_id","role","media_type","access_class","byte_size","sha256","producer_event_id","created_at_utc"]} for a in artifacts.values()]
    fingerprint = {"summary":summary,"events":sorted((e["event_id"],e["sha256"]) for e in events),"artifacts":sorted(immutable_artifacts,key=lambda a:a["artifact_id"])}
    return {"summary":summary,"events":events,"artifacts":list(artifacts.values()),"observations":observations,"starts":starts,"finishes":finishes,"terminal":terminal,"fingerprint":__import__("hashlib").sha256(canonical(fingerprint)).hexdigest()}
