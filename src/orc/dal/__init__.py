import sys


def warn_stub(name: str) -> None:
    print(f"warning: {name} provider is a stub — no real {name} integration is active", file=sys.stderr)
