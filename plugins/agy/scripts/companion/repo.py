"""companion.repo — which repository a companion invocation targets.

Every repo-scoped subcommand (`delegate`, `review`, `adversarial-review`,
`status`, `result`, `cancel`) resolves its repository from `--repo <path>`
when given, else the process **cwd**. `agy` launches with `cwd=repo_root`
and the **state dir** is keyed on `sha256(realpath(repo_root))`
(`companion.state.resolve_state_dir`), so naming the root is the whole of it.

Note the bound that gives you — the same one AGENTS.md records for
`--add-dir`: it scopes agy's *writes*, not its reads, since agy walks up to
parent `AGENTS.md`/`CONTEXT.md` regardless of cwd.

A **leaf module**: it imports no subcommand module, so every subcommand can
import it without a circular import.
"""

_REPO_HELP = (
    "Target git repository (default: the current working directory). Use this to "
    "drive the companion from outside the repo it acts on — a multi-repo workspace "
    "root, for instance."
)


def add_repo_argument(parser):
    """Register `--repo` on a subcommand's parser. `setup` deliberately does
    not use it: it checks the agy install, not a repository."""
    parser.add_argument("--repo", default=None, metavar="PATH", help=_REPO_HELP)


def resolve_repo(args):
    """The path this invocation targets: `--repo` when supplied, else the
    process cwd. `getattr` rather than `args.repo` so a caller that builds
    its own argparse namespace without the flag keeps working."""
    return getattr(args, "repo", None) or "."
