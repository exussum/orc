from typing import Any


def safe_eval(val: str, ns: dict[str, Any]) -> Any:
    return eval(val, ns)  # nosemgrep: python.lang.security.audit.eval-detected.eval-detected
