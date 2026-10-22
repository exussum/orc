# /// script
# requires-python = ">=3.12"
# ///
"""Copy GoogleFindMyTools' saved login into Bitwarden for orc's findhub provider.

    uv run scripts/seed_find_hub.py <secrets.json> [--project <id>] [--force]

Reads the four values orc.dal.findhub.nova needs from the secrets.json that
GoogleFindMyTools' Chrome login writes and creates the FIND_HUB_* secrets
that are missing. An existing secret is left alone unless --force, which
overwrites it. --project names the project to create in; it may be omitted
when the access token sees exactly one. The values never print.
"""

import json
import subprocess
import sys

NAMES = {
    "FIND_HUB_USERNAME": ("username",),
    "FIND_HUB_AAS_TOKEN": ("aas_token",),
    "FIND_HUB_ANDROID_ID": ("fcm_credentials", "gcm", "android_id"),
    "FIND_HUB_OWNER_KEY": ("owner_key",),
}


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 1:
        print(__doc__)
        return 2
    force = "--force" in argv
    project = argv[argv.index("--project") + 1] if "--project" in argv else None

    with open(args[0]) as f:
        saved = json.load(f)
    vault = {s["key"]: s for s in bws("secret", "list")}
    for name, path in NAMES.items():
        value = saved
        for key in path:
            value = value[key]
        value = str(value)
        if name in vault and not force:
            print(f"{name} present")
        elif name in vault:
            bws("secret", "edit", "--value", value, vault[name]["id"])
            print(f"{name} overwritten")
        else:
            project = project or sole_project()
            bws("secret", "create", name, value, project)
            print(f"{name} created")
    return 0


def sole_project() -> str:
    projects = bws("project", "list")
    if len(projects) != 1:
        raise SystemExit(f"the token sees {len(projects)} projects; pass --project <id>")
    return projects[0]["id"]


def bws(*args: str) -> list[dict[str, str]]:
    result = subprocess.run(["bws", *args], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"bws {args[0]} {args[1]} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else []


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
