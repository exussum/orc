import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

_ALSA_CONTROLS = ("Master", "PCM", "Speaker")


class UsbAudioDevice(NamedTuple):
    card_index: int
    card: str
    serial: str | None


def set_volume(serial: str, pct: int) -> None:
    if sys.platform == "darwin":
        import orc

        if len(orc.USB) > 1:
            print(f"warning: osascript ignores {serial!r} and adjusts the system default output device", file=sys.stderr)
        subprocess.run(["osascript", "-e", f"set volume output volume {pct}"], check=True)
    elif sys.platform.startswith("linux"):
        _alsa_mixer(serial).setvolume(pct)
    else:
        raise RuntimeError(f"No volume control implemented for platform {sys.platform!r}")


def get_volume(serial: str) -> int:
    if sys.platform == "darwin":
        out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], check=True, capture_output=True, text=True)
        return int(out.stdout.strip())
    elif sys.platform.startswith("linux"):
        return _alsa_mixer(serial).getvolume()[0]
    else:
        raise RuntimeError(f"No volume control implemented for platform {sys.platform!r}")


def _usb_serial(idx: int) -> str | None:
    usb_dir = Path(f"/sys/class/sound/card{idx}/device").resolve()
    for parent in (usb_dir, *usb_dir.parents):
        serial_file = parent / "serial"
        if serial_file.exists():
            return serial_file.read_text().strip()
    return None


def list_usb_audio_devices() -> list[UsbAudioDevice]:
    import alsaaudio

    return [UsbAudioDevice(idx, card, _usb_serial(idx)) for idx, card in enumerate(alsaaudio.cards())]


def card_index_for_serial(serial: str) -> int:
    devices = list_usb_audio_devices()
    for dev in devices:
        if dev.serial == serial:
            return dev.card_index
    raise RuntimeError(f"No ALSA card with USB serial {serial!r}: found {devices}")


def _alsa_mixer(serial: str) -> Any:
    import alsaaudio

    idx = card_index_for_serial(serial)
    for control in _ALSA_CONTROLS:
        try:
            return alsaaudio.Mixer(control=control, cardindex=idx)
        except alsaaudio.ALSAAudioError:
            continue
    raise RuntimeError(f"No usable mixer control {_ALSA_CONTROLS} on ALSA card {idx} (serial {serial!r})")
