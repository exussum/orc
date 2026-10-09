import subprocess
import sys
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Build the minified Tailwind CSS and the Mac installer into the wheel."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        subprocess.run(
            ["tailwindcss", "-i", "src/css/tailwind.src.css", "-o", "src/orc/static/tailwind.min.css", "--minify"],
            check=True,
        )
        build_data["force_include"]["src/orc/static/tailwind.min.css"] = "orc/static/tailwind.min.css"
        subprocess.run([sys.executable, "scripts/build_mac.py"], check=True)
        build_data["force_include"]["src/orc/static/meet-in-chrome.sh"] = "orc/static/meet-in-chrome.sh"
