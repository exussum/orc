#!/bin/sh

if [ -n "$(git status --porcelain)" ]; then
    echo "Error: uncommitted changes present" >&2
    git status --short >&2
    exit 1
fi

rm -f src/orc/static/tailwind.min.css
tailwindcss -i src/orc/static/tailwind.src.css -o src/orc/static/tailwind.min.css --minify

rm -rf data/dist
uv build --wheel data
uv pip install data/dist/orc_data-*.whl
printf 'SHA = "%s"\nBUILD_TIME = "%s"\n' "$(git rev-parse --short HEAD)" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" > src/orc/_build.py
rm -rf dist
uv pip install '.[build]'
uv build --wheel
uv run --no-sync twine upload -u a -p a --repository-url http://registry.int.exussum.org dist/orc-*.whl
git checkout src/orc/_build.py

if [ "$1" = "full" ]; then
    uv run twine upload -u a -p a --repository-url http://registry.int.exussum.org data/dist/orc_data-*.whl
fi
