import base64
import json
from collections.abc import Callable
from typing import Any

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

import orc
from orc import model as m
from orc.dal.push import Gone

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_TTL_SECONDS = 24 * 60 * 60
_GONE_STATUSES = frozenset({401, 403, 404, 410})


def public_key() -> str:
    point = Vapid.from_string(orc.config.secrets.vapid_private_key).public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(point).rstrip(b"=").decode()


def send(subscription: m.PushSubscription, title: str, body: str) -> None:
    try:
        webpush(
            {"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.public_key, "auth": subscription.auth_secret}},
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=Vapid.from_string(orc.config.secrets.vapid_private_key),
            vapid_claims={"sub": orc.config.settings.base_url},
            timeout=orc.config.settings.http_timeout,
            ttl=_TTL_SECONDS,
        )
    except WebPushException as exc:
        if exc.response is not None and exc.response.status_code in _GONE_STATUSES:
            raise Gone(subscription.endpoint) from exc
        raise
