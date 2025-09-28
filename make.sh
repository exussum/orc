#!/bin/sh

pip install twine==6.2.0
python3 -m build --wheel
twine upload -u a -p a --repository-url http://registry.exussum.org:8080 dist/orc-0.0.1-py2.py3-none-any.whl
