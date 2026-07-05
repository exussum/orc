import importlib
from urllib.parse import urlparse

import nh3

_ALLOWED_STREAM_DOMAINS = {".googlevideo.com"}


def safe_eval(val, ns):
    cls_name, _, member = val.partition(".")
    cls = ns.get(cls_name)
    if cls is None:
        raise ValueError(f"Unknown device: {cls_name!r}")
    return cls[member] if member else cls


def safe_import(val):
    module_path, fn_name = val.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), fn_name)  # nosemgrep: non-literal-import


def safe_html(html):
    return nh3.clean(html)


def safe_domain(url, allowed=_ALLOWED_STREAM_DOMAINS):
    host = urlparse(url).hostname or ""
    if not host or not any(host.endswith(s) for s in allowed):
        raise ValueError(f"Stream URL host must end with one of {allowed}, got: {host!r}")
    return url
