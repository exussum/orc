#!/bin/sh

rm -rf data/dist
uv build --wheel data
uv pip install data/dist/orc_data-*.whl
rm -rf dist
uv pip install '.[build]'
uv build --wheel
uv run twine upload -u a -p a --repository-url http://registry.int.exussum.org dist/orc-*.whl

if [ "$1" = "full" ]; then
    uv run twine upload -u a -p a --repository-url http://registry.int.exussum.org data/dist/orc_data-*.whl
fi
