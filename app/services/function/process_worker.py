"""One invocation per process; JSON transport never unpickles generated results."""

import contextlib
import json
import sys


def main():
    request = json.load(sys.stdin)
    with contextlib.redirect_stdout(sys.stderr):
        from fastapi.encoders import jsonable_encoder

        from app.services.function.runtime import _execute_code_snapshot

        try:
            output = _execute_code_snapshot(**request)
            result = json.dumps(
                {"ok": True, "output": jsonable_encoder(output)},
                ensure_ascii=False,
                allow_nan=False,
            )
        except Exception as exc:
            result = json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                },
                ensure_ascii=False,
            )
    print(result, flush=True)


if __name__ == "__main__":
    main()
