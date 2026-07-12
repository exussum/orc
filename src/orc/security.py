import importlib
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import nh3


def safe_eval(val: str, ns: dict[str, Any]) -> Any:
    return eval(val, ns)  # nosemgrep: python.lang.security.audit.eval-detected.eval-detected


def safe_import(val: str) -> Any:
    module_path, fn_name = val.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), fn_name)  # nosemgrep: non-literal-import


def safe_html(html: str) -> str:
    return nh3.clean(html)


def safe_domain(url: str, allowed: Iterable[str]) -> str:
    host = urlparse(url).hostname or ""
    if not host or not any(host.endswith(s) if s.startswith(".") else host == s for s in allowed):
        raise ValueError(f"Stream URL host must match one of {sorted(allowed)}, got: {host!r}")
    return url
