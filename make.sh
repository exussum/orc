#!/bin/sh

pip install -e '.[build]'
rm -rf dist
python3 -m build --wheel
python3 -m twine upload -u a -p a --repository-url http://registry.int.exussum.org:8080 dist/orc-*.whl

if [ "$1" = "full" ]; then
    rm -rf data/dist
    python3 -m build --wheel data
    python3 -m twine upload -u a -p a --repository-url http://registry.int.exussum.org:8080 data/dist/orc_data-*.whl
fi
