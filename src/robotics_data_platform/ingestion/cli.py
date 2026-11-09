"""Data-only CLI; capture source is mounted read-only and never executed."""
import argparse
import json
import os
from pathlib import Path

from robotics_data_platform.database import build_engine
from robotics_data_platform.ingestion.repository import counts
from robotics_data_platform.ingestion.storage import atomic_write,canonical
from robotics_data_platform.ingestion.worker import Receiver
from robotics_data_platform.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["ingest","replay","reconcile","counts"])
    parser.add_argument("--capture-root",type=Path,default=Path(os.environ.get("RDP_CAPTURE_ROOT","/capture")))
    parser.add_argument("--raw-root",type=Path,default=Path(os.environ.get("RDP_RAW_ROOT","/raw")))
    parser.add_argument("--stale-after-seconds",type=float,default=300)
    args = parser.parse_args()
    engine = build_engine(Settings())
    try:
        if args.command == "counts":
            print(json.dumps(counts(engine)))
            return
        receiver = Receiver(engine,args.raw_root)
        if args.command == "replay":
            results = receiver.replay()
        else:
            results = receiver.scan(args.capture_root,args.stale_after_seconds if args.command == "reconcile" else None)
            if args.command == "reconcile":
                results += receiver.replay()
        summary = {"processed":len(results),"statuses":{status:sum(r["status"] == status for r in results) for status in sorted({r["status"] for r in results})},"results":results,"robot_execution":False}
        atomic_write(args.raw_root/"last-run.json",canonical(summary))
        print(json.dumps(summary),flush=True)
        if any(r["status"] not in {"INGESTED","DUPLICATE"} for r in results):
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
