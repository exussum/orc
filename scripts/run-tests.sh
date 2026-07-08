#!/bin/sh
set -e
~/.venv-orc/bin/pytest "$@"
~/.venv-orc/bin/pytest entrance_sensor "$@"
