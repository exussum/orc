from __future__ import annotations

import asyncio
import logging
import os
import signal
import ssl
import tempfile
import threading
from typing import Any

from amqtt.broker import Broker

_log = logging.getLogger(__name__)
_thread: threading.Thread | None = None
_server_pem: bytes = b""


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


def _patch_ssl_context_from_memory() -> None:
    """Build the listener's TLS context from the in-memory server PEM.

    amqtt's ``_create_ssl_context`` reads ``certfile``/``keyfile`` off disk and sets
    verify_mode=CERT_OPTIONAL. We hold the cert+key in memory (from BWS), so replace
    it: load the chain via a temp file that exists only for the ``load_cert_chain``
    call, and force CERT_NONE — we don't validate the device's client cert (it's on
    our own network).
    """
    if getattr(Broker, "_lg_ac_cert_patched", False):
        return

    def _create_ssl_context(listener: Any) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        fd, path = tempfile.mkstemp(prefix="lg_ac_", suffix=".pem")
        try:
            os.write(fd, _server_pem)
            os.close(fd)
            context.load_cert_chain(path)
        finally:
            os.unlink(path)
        context.verify_mode = ssl.CERT_NONE
        return context

    Broker._create_ssl_context = staticmethod(_create_ssl_context)
    Broker._lg_ac_cert_patched = True


def start(mqtts_port: int, cert_pem: bytes, key_pem: bytes, plain_port: int = 1883) -> None:
    """Run the embedded broker on a background asyncio loop.

    The device connects over TLS on `mqtts_port`, where we present the server cert
    (cert_pem + key_pem, kept in memory); its own client cert is accepted without
    validation. Our paho client connects on the plain localhost listener.
    """
    global _server_pem
    _server_pem = cert_pem.rstrip() + b"\n" + key_pem.rstrip() + b"\n"

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
            },
        },
        "sys_interval": 0,
        "auth": {"allow-anonymous": True},
    }

    _patch_mqtt31()
    _patch_ssl_context_from_memory()

    async def _serve() -> None:
        broker = Broker(config)
        await broker.start()
        await asyncio.Event().wait()  # keep the broker's listeners running

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        except Exception:
            # The broker is essential (the AC can't connect without it). If it dies,
            # signal a process restart rather than silently losing AC connectivity.
            _log.exception("lg_ac broker thread crashed; signaling restart")
            os.kill(os.getpid(), signal.SIGTERM)

    global _thread
    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()
