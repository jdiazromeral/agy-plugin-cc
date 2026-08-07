"""companion.cli — subcommand dispatch for the agy companion.

A subcommand is a sibling module exposing `HELP`, `add_arguments(parser)`,
and `run(args) -> int`, plus one line in _SUBCOMMANDS below — this dispatch
loop does not change. Append entries, never reorder them: two branches
extending the table merge as a trivial union only if both append.
"""
import argparse
import sys

from companion import delegate as delegate_command
from companion import review as review_command
from companion import setup as setup_command
from companion import status as status_command
from companion import result as result_command
from companion import cancel as cancel_command
from companion import adversarial_review as adversarial_review_command

_SUBCOMMANDS = {
    "setup": setup_command,
    "review": review_command,
    "status": status_command,
    "result": result_command,
    "cancel": cancel_command,
    "delegate": delegate_command,
    "adversarial-review": adversarial_review_command,
}


def _build_parser():
    parser = argparse.ArgumentParser(prog="agy_companion")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    for name, module in _SUBCOMMANDS.items():
        subparser = subparsers.add_parser(name, help=module.HELP)
        module.add_arguments(subparser)
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    module = _SUBCOMMANDS[args.subcommand]
    return module.run(args)


if __name__ == "__main__":
    sys.exit(main())
