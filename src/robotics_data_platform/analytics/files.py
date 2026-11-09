"""Versioned artifact utilities usable without Arrow, DuckDB or Prefect."""
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
from uuid import UUID

from robotics_data_platform.ingestion.storage import atomic_write,canonical


def normalized(value):
    if isinstance(value,dict):
        return {str(k):normalized(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [normalized(v) for v in value]
    if isinstance(value,UUID):
        return str(value)
    if isinstance(value,datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return value


def digest(value):
    return hashlib.sha256(canonical(normalized(value))).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda:source.read(65536),b""):
            result.update(block)
    return result.hexdigest()


def immutable_json(path,value):
    data = canonical(normalized(value))
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Immutable analytical object conflict")
    else:
        atomic_write(path,data)


def checked_hash(value):
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Expected a SHA-256 identity")
    return value


def load_input(root,identity):
    path = root/"inputs"/(checked_hash(identity)+".json")
    if file_hash(path) != identity:
        raise ValueError("Frozen input checksum mismatch")
    return json.loads(path.read_bytes())


def verify_published(root,fingerprint,manifest_sha=None):
    directory = root/"versions"/checked_hash(fingerprint)
    path = directory/"manifest.json"
    if manifest_sha and file_hash(path) != manifest_sha:
        raise ValueError("Published manifest checksum mismatch")
    manifest = json.loads(path.read_bytes())
    if manifest["build_fingerprint"] != fingerprint:
        raise ValueError("Published version identity mismatch")
    load_input(root,manifest["input_sha256"])
    for name,item in manifest["tables"].items():
        if name not in {"episodes","events","artifacts","observations","actions","findings","evaluation_cases","proposals","proposal_artifacts","proposal_validations","proposal_receipts","proposal_findings"}:
            raise ValueError("Unknown analytical table")
        if file_hash(directory/(name+".parquet")) != item["file_sha256"]:
            raise ValueError("Published Parquet checksum mismatch: "+name)
    return manifest
