"""Explicit simulator and local-capture inspection commands."""
import argparse
import json
import os
from pathlib import Path

from robotics_data_platform.simulator.capture import atomic_write, canonical, export_episode, managed_episode, terminal_measurements, verify_episode
from robotics_data_platform.simulator.runner import execute_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root",type=Path,default=Path(os.environ.get("RDP_CAPTURE_ROOT","/capture")))
    commands = parser.add_subparsers(dest="command",required=True)
    run = commands.add_parser("run")
    run.add_argument("--task-file",type=Path)
    run.add_argument("--object-id",default="package_01")
    run.add_argument("--destination-id",default="zone_a")
    run.add_argument("--seed",type=int,default=7)
    run.add_argument("--scenario",choices=["normal","no_grasp"],default="normal")
    suite = commands.add_parser("suite")
    suite.add_argument("--cases",type=Path,default=Path("examples/simulator-suite.json"))
    commands.add_parser("list")
    verify = commands.add_parser("verify")
    verify.add_argument("episode_id")
    export = commands.add_parser("export")
    export.add_argument("episode_id")
    export.add_argument("--output-dir",type=Path,required=True)
    args = parser.parse_args()
    root = args.capture_root
    if args.command == "run":
        request = args.task_file.read_bytes() if args.task_file else {"object_id":args.object_id,"destination_id":args.destination_id,"seed":args.seed,"scenario":args.scenario}
        result = execute_request(request,root)
        print(json.dumps(result),flush=True)
        raise SystemExit(0 if result["outcome"] == "SUCCEEDED" else 1)
    if args.command == "suite":
        cases = json.loads(args.cases.read_bytes())["cases"]
        results = []
        for case in cases:
            result = execute_request(case["request"],root)
            verify_episode(managed_episode(root,result["episode_id"]))
            row = {"case_id":case["case_id"],"request":case["request"],"expected_outcome":case["expected_outcome"],"expected_failure_code":case.get("expected_failure_code"),"actual":result}
            row["passed"] = result["outcome"] == case["expected_outcome"] and result["failure_code"] == case.get("expected_failure_code")
            row["terminal_measurements"] = terminal_measurements(root,result["episode_id"])
            if case.get("repeat_of"):
                original = next(r for r in results if r["case_id"] == case["repeat_of"])
                row["repeat_measurements_match"] = row["terminal_measurements"] == original["terminal_measurements"]
                row["passed"] = row["passed"] and row["repeat_measurements_match"]
            results.append(row)
            print(json.dumps(row),flush=True)
        report = {"suite_version":"1.0.0","cases":len(results),"passed":sum(row["passed"] for row in results),"results":results,"limits":"Development task/capture checks, not the final frozen evaluation or ingestion durability validation."}
        atomic_write(root/"suite-result.json",canonical(report))
        raise SystemExit(0 if all(row["passed"] for row in results) else 1)
    if args.command == "list":
        for path in sorted((root/"episodes").glob("*")):
            file = path/"episode.json"
            print(file.read_text() if file.exists() else json.dumps({"episode_id":path.name,"capture_state":"OPEN_OR_INTERRUPTED"}))
    elif args.command == "verify":
        print(json.dumps(verify_episode(managed_episode(root,args.episode_id))))
    elif args.command == "export":
        print(export_episode(root,args.episode_id,args.output_dir))


if __name__ == "__main__":
    main()
