"""Entry point invoked only inside isolated_probe's fresh namespace."""

import contextlib
import json
import resource
import sys
from pathlib import Path


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    payload = json.load(sys.stdin)
    from app.services.function.runtime_probe import FunctionRuntimeProbe

    print(json.dumps({"event": "ready"}), flush=True)
    # User prints are not the check report and do not become assistant text.
    with open("/dev/null", "w") as sink, contextlib.redirect_stdout(sink):
        passed, error, result_type = FunctionRuntimeProbe().run(
            workspace_dir=Path("/work"),
            payload=payload,
            context={"scope": {}, "trace_id": "isolated-probe", "execution_mode": "plan"},
        )
    print(
        json.dumps(
            {"event": "result", "passed": passed, "error": error, "result_type": result_type}
        )
    )


if __name__ == "__main__":
    main()
