#!/bin/sh

pip install twine==6.2.0 build==1.5.0
rm -rf dist
python3 -m build --wheel
twine upload -u a -p a --repository-url http://registry.exussum.org:8080 dist/orc-*.whl

if [ "$1" = "full" ]; then
    rm -rf data/dist
    python3 -m build --wheel data
    twine upload -u a -p a --repository-url http://registry.exussum.org:8080 data/dist/orc_data-*.whl
fi
