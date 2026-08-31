import subprocess
import sys
from typing import Any

_ALSA_CONTROLS = ("Master", "PCM", "Speaker")


def set_volume(name: str, pct: int) -> None:
    if sys.platform == "darwin":
        import orc

        if len(orc.USB) > 1:
            print(f"warning: osascript ignores {name!r} and adjusts the system default output device", file=sys.stderr)
        subprocess.run(["osascript", "-e", f"set volume output volume {pct}"], check=True)
    elif sys.platform.startswith("linux"):
        _alsa_mixer(name).setvolume(pct)
    else:
        raise RuntimeError(f"No volume control implemented for platform {sys.platform!r}")


def get_volume(name: str) -> int:
    if sys.platform == "darwin":
        out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], check=True, capture_output=True, text=True)
        return int(out.stdout.strip())
    elif sys.platform.startswith("linux"):
        return _alsa_mixer(name).getvolume()[0]
    else:
        raise RuntimeError(f"No volume control implemented for platform {sys.platform!r}")


def _alsa_mixer(name: str) -> Any:
    import alsaaudio

    cards = alsaaudio.cards()
    try:
        idx = next(i for i, card in enumerate(cards) if name in card)
    except StopIteration:
        raise RuntimeError(f"No ALSA card matching {name!r}: found {cards}") from None
    for control in _ALSA_CONTROLS:
        try:
            return alsaaudio.Mixer(control=control, cardindex=idx)
        except alsaaudio.ALSAAudioError:
            continue
    raise RuntimeError(f"No usable mixer control {_ALSA_CONTROLS} on ALSA card {name!r}")
