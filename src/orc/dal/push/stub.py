import base64
from functools import cache

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from orc import model as m
from orc.dal import warn_stub

warn_stub("push")


@cache
def public_key() -> str:
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(point).rstrip(b"=").decode()


def send(subscription: m.PushSubscription, title: str, body: str) -> None:
    pass
