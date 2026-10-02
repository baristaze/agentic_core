"""The session runner's binary with the runner suites' kind and tool, as a
product's own entry point passes its catalog: the same `main`, run as a
process of its own over the compose stack by the end-to-end suite.

    python workers/session_runner/tests/e2e_runner.py serve"""

import sys


def main() -> int:
    # The trust store before anything that binds ssl is imported, as the
    # binary's own entry point installs it.
    from acme.infra.trust import install_trust_store

    install_trust_store()
    from runner_support import KINDS, TOOLS

    from acme.workers.session_runner.main import main as run

    return run(agent_kinds=KINDS, tool_catalog=TOOLS)


if __name__ == "__main__":
    sys.exit(main())
