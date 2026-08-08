export UV_PROJECT_ENVIRONMENT="$HOME/.venv-orc"
export UV_LINK_MODE=copy
export UV_TRUSTED_HOST="$ORC_TRUSTED_HOST"
export VIRTUAL_ENV="$HOME/.venv-orc"
UV="$HOME/.local/bin/uv"
INSTALL_OPTS="--no-deps --index-url $ORC_REGISTRY_URL --no-cache"

supervisorctl stop orc

$UV pip install -r /tmp/pyproject.toml --no-sources --extra-index-url "$ORC_REGISTRY_URL" --no-cache
$UV pip install orc==0.0.1 --reinstall-package orc $INSTALL_OPTS
$UV pip install orc_plugins==0.0.1 --reinstall-package orc_plugins $INSTALL_OPTS

supervisorctl start orc

tail -f /var/log/orc.log
