#!/bin/sh

tailwindcss -i src/orc/static/tailwind.src.css -o src/orc/static/tailwind.min.css --minify

rm -rf data/dist
uv build --wheel data
uv pip install data/dist/orc_data-*.whl
rm -rf dist
uv pip install '.[build]'
uv build --wheel
