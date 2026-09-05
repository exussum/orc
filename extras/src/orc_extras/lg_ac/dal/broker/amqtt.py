from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from amqtt.broker import Broker

_thread: threading.Thread | None = None


def _patch_mqtt31() -> None:
    """Let amqtt accept the device's MQTT 3.1 CONNECT.

    amqtt hardcodes `proto_name != "MQTT"` (3.1.1). The AC sends the 3.1 name
    "MQIsdp"/level 3, so normalize it to "MQTT"/level 4 as the CONNECT is decoded;
    the rest of amqtt then treats it as 3.1.1.
    """
    from amqtt.mqtt.connect import ConnectVariableHeader

    if getattr(ConnectVariableHeader, "_lg_ac_patched", False):
        return
    original = ConnectVariableHeader.from_stream

    async def from_stream(cls: Any, reader: Any, fixed_header: Any) -> Any:
        header = await original(reader, fixed_header)
        if header.proto_name == "MQIsdp":
            header.proto_name = "MQTT"
            header.proto_level = 0x04
        return header

    ConnectVariableHeader.from_stream = classmethod(from_stream)
    ConnectVariableHeader._lg_ac_patched = True


def _patch_accept_any_client_cert() -> None:
    """Don't validate the device's client cert.

    amqtt hardcodes verify_mode=CERT_OPTIONAL, which fails the TLS handshake when
    the device presents a client cert not signed by our CA. We don't need mTLS
    from the device (it's on our own network), so force CERT_NONE.
    """
    import ssl

    from amqtt.broker import Broker

    if getattr(Broker, "_lg_ac_cert_patched", False):
        return
    original = Broker._create_ssl_context

    def _create_ssl_context(listener: Any) -> Any:
        context = original(listener)
        context.verify_mode = ssl.CERT_NONE
        return context

    Broker._create_ssl_context = staticmethod(_create_ssl_context)
    Broker._lg_ac_cert_patched = True


def start(mqtts_port: int, cafile: str, certfile: str, keyfile: str, plain_port: int = 1883) -> None:
    """Run the embedded broker on a background asyncio loop.

    The device connects over TLS on `mqtts_port` (our CA-signed server cert; its
    client cert is validated against `cafile`). Our own paho client connects on
    the plain localhost listener.
    """
    # "default" is amqtt's template listener that others inherit from — keep it
    # PLAIN (our local client), and override ssl only on the named device listener.
    config = {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"127.0.0.1:{plain_port}",
            },
            "device": {
                "type": "tcp",
                "bind": f"0.0.0.0:{mqtts_port}",
                "ssl": True,
                "cafile": cafile,
                "certfile": certfile,
                "keyfile": keyfile,
            },
        },
        "sys_interval": 0,
        "auth": {"allow-anonymous": True},
    }

    logging.getLogger("amqtt").setLevel(logging.WARNING)
    _patch_mqtt31()
    _patch_accept_any_client_cert()

    async def _serve() -> None:
        broker = Broker(config)
        await broker.start()
        await asyncio.Event().wait()  # keep the broker's listeners running

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())

    global _thread
    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()
