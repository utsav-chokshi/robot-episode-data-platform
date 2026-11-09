"""Immutable content objects, durable source receipts and data-only replay."""
import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from robotics_data_platform.ingestion.contracts import DataIssue, load_json


def canonical(data):
    return json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(path.name+"."+uuid4().hex+".partial")
    with temporary.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary,path)
    descriptor = os.open(path.parent,os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def receiver_lock(root):
    root.mkdir(parents=True,exist_ok=True)
    with (root/".receiver.lock").open("a") as file:
        fcntl.flock(file,fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(file,fcntl.LOCK_UN)


def safe_relative(root,value):
    if len(value) > 240 or "\x00" in value:
        raise DataIssue("PATH_INVALID","Path exceeds managed contract",value[:240])
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or "\\" in value or ":" in value or any(part in {".",".."} for part in value.split("/")):
        raise DataIssue("PATH_INVALID","Managed relative path required",value)
    target = root.joinpath(*relative.parts)
    if any(path.is_symlink() for path in [target,*target.parents] if path == root or root in path.parents):
        raise DataIssue("PATH_INVALID","Symlink not accepted",value)
    if not target.resolve().is_relative_to(root.resolve()):
        raise DataIssue("PATH_INVALID","Path escapes managed root",value)
    return target


class RawStore:
    def __init__(self,root: Path,min_free_bytes=134217728):
        self.root, self.min_free_bytes = root, min_free_bytes
        root.mkdir(parents=True,exist_ok=True)

    def object_path(self,digest):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise DataIssue("HASH_INVALID","Invalid content hash")
        return self.root/"objects"/"sha256"/digest[:2]/digest

    def put(self,data):
        digest = sha(data)
        path = self.object_path(digest)
        if path.exists():
            if path.read_bytes() != data:
                raise DataIssue("RAW_OBJECT_CORRUPT","Existing raw object differs from its address",digest)
        else:
            if shutil.disk_usage(self.root).free - len(data) < self.min_free_bytes:
                raise OSError("Raw storage low-space threshold reached; capture source retained")
            atomic_write(path,data)
        return digest

    def get(self,digest):
        try:
            data = self.object_path(digest).read_bytes()
        except FileNotFoundError as error:
            raise DataIssue("RAW_OBJECT_MISSING","Raw object missing",digest) from error
        if sha(data) != digest:
            raise DataIssue("RAW_OBJECT_CORRUPT","Raw object checksum mismatch",digest)
        return data

    def preserve(self,episode_path,partial=False):
        episode_id = str(UUID(episode_path.name))
        entries,issues = {},[]
        if partial:
            paths = ["attempt.json"] + [p.relative_to(episode_path).as_posix() for p in sorted((episode_path/"events").glob("*.json"))] + [p.relative_to(episode_path).as_posix() for p in sorted((episode_path/"objects/sha256").glob("*/*")) if p.is_file()]
            manifest_bytes = canonical({"partial_capture":True,"episode_id":episode_id})
            issues.append({"code":"CAPTURE_INCOMPLETE","message":"Stale capture has no final manifest; execution outcome unknown","reference":None})
        else:
            manifest_path = safe_relative(episode_path,"manifest.json")
            if manifest_path.stat().st_size > 1048576:
                raise DataIssue("CAPTURE_LIMIT_EXCEEDED","Manifest exceeds admission limit",episode_id)
            manifest_bytes = manifest_path.read_bytes()
            paths = []
            try:
                raw = load_json(manifest_bytes)
                for entry in raw.get("events",[]) + raw.get("artifacts",[]):
                    if not isinstance(entry,dict) or not isinstance(entry.get("relative_path"),str):
                        raise ValueError("Invalid file reference")
                    paths.append(entry["relative_path"])
                if len(paths) > 2000:
                    raise ValueError("Too many bundle entries")
            except (ValueError,TypeError,AttributeError) as error:
                issues.append({"code":"MANIFEST_INVALID","message":str(error)[:256],"reference":None})
        manifest_hash = self.put(manifest_bytes)
        for relative in sorted(set(paths)):
            try:
                path = safe_relative(episode_path,relative)
                size = path.stat().st_size
                maximum = 262144 if relative.startswith("events/") else 8388608
                if size > maximum:
                    raise DataIssue("CAPTURE_LIMIT_EXCEEDED","File exceeds admission limit; original remains in capture spool",relative)
                data = path.read_bytes()
            except FileNotFoundError:
                issues.append({"code":"ARTIFACT_MISSING","message":"Referenced capture file missing","reference":relative})
            except DataIssue as error:
                if error.code.startswith("RAW_OBJECT"):
                    raise
                issues.append({"code":error.code,"message":str(error),"reference":relative})
            except OSError:
                issues.append({"code":"ARTIFACT_UNREADABLE","message":"Referenced capture file could not be read","reference":relative[:240]})
            else:
                # Storage failures must stop admission, not label source data bad.
                entries[relative] = {"sha256":self.put(data),"byte_size":len(data)}
        receipt = {"receipt_version":"1.0.0","episode_id":episode_id,"manifest_sha256":manifest_hash,"files":entries,"source_issues":issues,"partial_capture":partial}
        receipt_id = sha(canonical(receipt))
        path = self.root/"receipts"/(receipt_id+".json")
        if not path.exists():
            atomic_write(path,canonical(receipt))
        return receipt_id,receipt

    def admission_rejection(self,episode_id,issue):
        diagnostic = {"admission_rejected":True,"code":issue.code,"message":str(issue),"reference":issue.reference}
        digest = self.put(canonical(diagnostic))
        receipt = {"receipt_version":"1.0.0","episode_id":episode_id,"manifest_sha256":digest,"files":{},"source_issues":[diagnostic],"partial_capture":False}
        identity = sha(canonical(receipt))
        path = self.root/"receipts"/(identity+".json")
        if not path.exists():
            atomic_write(path,canonical(receipt))
        return identity,receipt

    def read_receipt(self,path):
        data = path.read_bytes()
        if sha(data) != path.stem:
            raise DataIssue("RECEIPT_CORRUPT","Receipt identity mismatch",path.stem)
        return load_json(data)

    def status(self,identity,value):
        atomic_write(self.root/"statuses"/(identity+".json"),canonical(value))
