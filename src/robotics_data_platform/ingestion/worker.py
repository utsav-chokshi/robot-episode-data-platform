"""Receipt preservation, idempotent ingestion and restart reconciliation."""
import time
from pathlib import Path

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from robotics_data_platform.ingestion.contracts import DataIssue
from robotics_data_platform.ingestion.repository import project,record_issue
from robotics_data_platform.ingestion.storage import RawStore,receiver_lock
from robotics_data_platform.ingestion.validation import validate_receipt


class Receiver:
    def __init__(self,engine,raw_root: Path,checkpoint=None):
        self.engine,self.store = engine,RawStore(raw_root)
        self.checkpoint = checkpoint or (lambda stage:None)

    def _process(self,identity,receipt):
        issue = None
        try:
            bundle = validate_receipt(self.store,receipt)
        except DataIssue as error:
            issue = error
        except (ValueError,TypeError,KeyError,UnicodeError) as error:
            issue = DataIssue("CONTRACT_INVALID","Malformed capture content",type(error).__name__)
        try:
            if issue is None:
                try:
                    status = project(self.engine,identity,receipt,bundle)
                except DataIssue as error:
                    issue = error
                except IntegrityError:
                    issue = DataIssue("REFERENCE_ID_CONFLICT","A normalized identity or relationship conflicts with accepted data")
            if issue:
                status = "PENDING_REFERENCES" if issue.pending else "CONFLICTED" if issue.code.endswith("CONFLICT") else "QUARANTINED"
                record_issue(self.engine,identity,receipt,issue,status)
        except SQLAlchemyError:
            result = {"receipt_sha256":identity,"episode_id":receipt["episode_id"],"status":"PENDING_DB","reason":"Database unavailable or projection not committed; raw receipt retained"}
            self.store.status(identity,result)
            return result
        self.checkpoint("db_committed")
        result = {"receipt_sha256":identity,"episode_id":receipt["episode_id"],"status":status,"code":issue.code if issue else None}
        self.store.status(identity,result)
        return result

    def ingest(self,path: Path,partial=False):
        with receiver_lock(self.store.root):
            try:
                identity,receipt = self.store.preserve(path,partial=partial)
            except DataIssue as issue:
                identity,receipt = self.store.admission_rejection(path.name,issue)
            self.checkpoint("raw_persisted")
            return self._process(identity,receipt)

    def replay(self):
        results = []
        with receiver_lock(self.store.root):
            for path in sorted((self.store.root/"receipts").glob("*.json")):
                receipt = self.store.read_receipt(path)
                results.append(self._process(path.stem,receipt))
        return results

    def scan(self,capture_root: Path,stale_after_seconds=None):
        results = []
        for path in sorted((capture_root/"episodes").glob("*")):
            if not path.is_dir() or path.is_symlink():
                continue
            if (path/"manifest.json").is_file():
                results.append(self.ingest(path))
            elif stale_after_seconds is not None and (path/"attempt.json").exists():
                files = [p.stat().st_mtime for p in path.rglob("*") if p.is_file()]
                if files and time.time()-max(files) >= stale_after_seconds:
                    results.append(self.ingest(path,partial=True))
        return results
