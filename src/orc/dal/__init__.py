import os
import sys


def warn_stub(name: str) -> None:
    if os.environ.get("ORC_QUIET_STUBS"):
        return
    print(f"warning: {name} provider is a stub — no real {name} integration is active", file=sys.stderr)
