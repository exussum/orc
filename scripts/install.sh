export UV_PROJECT_ENVIRONMENT=/root/.venv-orc
export UV_LINK_MODE=copy
export UV_TRUSTED_HOST=registry.example.local
export VIRTUAL_ENV=/root/.venv-orc
UV=/root/.local/bin/uv
INSTALL_OPTS="--no-deps --index-url http://registry.example.local --no-cache"

curl -sSL https://raw.githubusercontent.com/exussum/orc/main/pyproject.toml -o /tmp/pyproject.toml

supervisorctl stop orc

$UV pip install orc==0.0.1 --reinstall-package orc $INSTALL_OPTS
$UV pip install orc_entrance_sensor==0.0.1 --reinstall-package orc_entrance_sensor $INSTALL_OPTS
$UV sync --no-install-project --no-cache --directory /tmp

supervisorctl start orc
tail -f /var/log/orc.log
