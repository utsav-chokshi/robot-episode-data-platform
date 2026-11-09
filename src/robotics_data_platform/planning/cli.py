"""Local experimental inference, independent of robot execution and SQL writes."""
import argparse
import json
from pathlib import Path
from robotics_data_platform.planning.adapter import SmolProvider
from robotics_data_platform.planning.attempts import Vocabulary,capture_attempt,observation_source,verify_attempt


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='operation',required=True)
    run = commands.add_parser('run')
    run.add_argument('--source-episode-id',required=True)
    run.add_argument('--observation-id',required=True)
    run.add_argument('--instruction',required=True)
    run.add_argument('--vocabulary',type=Path,default=Path('/app/examples/proposals/vocabulary.json'))
    run.add_argument('--raw-root',type=Path,default=Path('/raw'))
    run.add_argument('--timeout-seconds',type=float,default=120)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('proposal_id')
    for command in (run,inspect):
        command.add_argument('--proposal-root',type=Path,default=Path('/proposals'))
    args = parser.parse_args()
    if args.operation == 'inspect':
        result = verify_attempt(args.proposal_root,args.proposal_id)
    else:
        vocabulary = Vocabulary.model_validate_json(args.vocabulary.read_bytes())
        image,source = observation_source(args.raw_root,args.source_episode_id,args.observation_id)
        result = capture_attempt(args.proposal_root,image,source,args.instruction,vocabulary,SmolProvider(),args.timeout_seconds)
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':
    main()
