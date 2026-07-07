#!/bin/sh

export TWINE_USERNAME=a
export TWINE_PASSWORD=a
export TWINE_REPOSITORY_URL=http://registry.example.local

TWINE="uv run --no-sync twine upload"

rm -f src/orc/static/tailwind.min.css
tailwindcss -i src/css/tailwind.src.css -o src/orc/static/tailwind.min.css --minify

if [ -n "$(git status --porcelain)" ]; then
    echo "Error: uncommitted changes present" >&2
    git status --short >&2
    exit 1
fi

rm -rf data/dist
uv build --wheel data
uv pip install data/dist/orc_data-*.whl

rm -rf entrance_sensor/dist
uv build --wheel entrance_sensor

printf 'SHA = "%s"\nBUILD_TIME = "%s"\n' "$(git rev-parse --short HEAD)" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" > src/orc/_build.py
rm -rf dist
uv pip install '.[build]'
uv build --wheel
$TWINE dist/orc-*.whl
git checkout src/orc/_build.py

if [ "$1" = "full" ]; then
    $TWINE data/dist/orc_data-*.whl entrance_sensor/dist/orc_entrance_sensor-*.whl
fi
