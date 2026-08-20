#!/bin/sh

if [ ! -f scripts/deploy.env ]; then
    echo "scripts/deploy.env missing — copy scripts/deploy.env.example and set your hosts" >&2
    exit 1
fi
. scripts/deploy.env

export UV_PUBLISH_USERNAME=a
export UV_PUBLISH_PASSWORD=a
export UV_PUBLISH_URL="$ORC_REGISTRY_URL"

git checkout src/orc/_build.py
rm -rf src/orc/static/tailwind.min.css dist

tailwindcss -i src/css/tailwind.src.css -o src/orc/static/tailwind.min.css --minify

if [ -n "$(git status --porcelain)" ]; then
    echo "Error: uncommitted changes present" >&2
    git status --short >&2
    exit 1
fi

printf 'SHA = "%s"\nBUILD_TIME = "%s"\n' "$(git rev-parse --short HEAD)" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" > src/orc/_build.py
echo data extras . | xargs -n 1 uv build --wheel --out-dir dist
uv pip install dist/orc_data-*.whl

uv publish dist/orc-*.whl dist/orc_extras-*.whl

if [ "$1" = "full" ]; then
    uv publish dist/orc_data-*.whl
fi

git checkout src/orc/_build.py
