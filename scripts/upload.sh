#!/bin/sh

if [ ! -f scripts/deploy.env ]; then
    echo "scripts/deploy.env missing — copy scripts/deploy.env.example and set your hosts" >&2
    exit 1
fi
. scripts/deploy.env

export TWINE_USERNAME=a
export TWINE_PASSWORD=a
export TWINE_REPOSITORY_URL="$ORC_REGISTRY_URL"
TWINE="uv run --no-sync twine upload"

git checkout src/orc/_build.py
rm -rf src/orc/static/tailwind.min.css dist

tailwindcss -i src/css/tailwind.src.css -o src/orc/static/tailwind.min.css --minify

if [ -n "$(git status --porcelain)" ]; then
    echo "Error: uncommitted changes present" >&2
    git status --short >&2
    exit 1
fi

printf 'SHA = "%s"\nBUILD_TIME = "%s"\n' "$(git rev-parse --short HEAD)" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" > src/orc/_build.py
echo data plugins . | xargs -n 1 uv build --wheel --out-dir dist
uv pip install dist/orc_data-*.whl

$TWINE dist/orc-*.whl dist/orc_plugins-*.whl

if [ "$1" = "full" ]; then
    $TWINE dist/orc_data-*.whl
fi

git checkout src/orc/_build.py
