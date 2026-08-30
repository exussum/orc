import subprocess
import sys

_ALSA_CONTROLS = ("Master", "PCM", "Speaker")


def set_volume(name: str, pct: int) -> None:
    if sys.platform == "darwin":
        import orc

        if len(orc.USB) > 1:
            print(f"warning: osascript ignores {name!r} and adjusts the system default output device", file=sys.stderr)
        subprocess.run(["osascript", "-e", f"set volume output volume {pct}"], check=True)
    elif sys.platform.startswith("linux"):
        _set_alsa_volume(name, pct)
    else:
        raise RuntimeError(f"No volume control implemented for platform {sys.platform!r}")


def _set_alsa_volume(name: str, pct: int) -> None:
    import alsaaudio

    cards = alsaaudio.cards()
    try:
        idx = next(i for i, card in enumerate(cards) if name in card)
    except StopIteration:
        raise RuntimeError(f"No ALSA card matching {name!r}: found {cards}") from None
    for control in _ALSA_CONTROLS:
        try:
            alsaaudio.Mixer(control=control, cardindex=idx).setvolume(pct)
            return
        except alsaaudio.ALSAAudioError:
            continue
    raise RuntimeError(f"No usable mixer control {_ALSA_CONTROLS} on ALSA card {name!r}")
