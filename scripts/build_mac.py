from pathlib import Path

SOURCE = Path("src/mac")
OUTPUT = Path("src/orc/static/meet-in-chrome.sh")


def build() -> Path:
    script = (SOURCE / "install.sh").read_text()
    applescript = (SOURCE / "meet-in-chrome.applescript").read_text()
    OUTPUT.write_text(script.replace("@APPLESCRIPT@\n", applescript))
    return OUTPUT


if __name__ == "__main__":
    build()
