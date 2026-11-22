set -e

export UV_PROJECT_ENVIRONMENT="$HOME/.venv-orc"
export UV_LINK_MODE=copy
export UV_TRUSTED_HOST="$ORC_TRUSTED_HOST"
export VIRTUAL_ENV="$HOME/.venv-orc"
UV="$HOME/.local/bin/uv"
INSTALL_OPTS="--no-deps --index-url $ORC_REGISTRY_URL --no-cache"
RESTART=1
for arg in "$@"; do
    case "$arg" in
        --no-restart) RESTART= ;;
        *) echo "unknown option: $arg" >&2; exit 1 ;;
    esac
done

if [ "$RESTART" ]; then
    supervisorctl stop orc || true
fi

DEPS=$(mktemp -d)
mkdir "$DEPS/extras" "$DEPS/data"
cp /tmp/pyproject.toml "$DEPS/pyproject.toml"
cp /tmp/extras-pyproject.toml "$DEPS/extras/pyproject.toml"
cp /tmp/data-pyproject.toml "$DEPS/data/pyproject.toml"
sed -i '/github.com\/exussum/d' "$DEPS/pyproject.toml"
UV_INDEX="$ORC_REGISTRY_URL/simple/" $UV sync --no-install-workspace --group dev --no-cache --project "$DEPS"
rm -rf "$DEPS"
$UV pip install orc==0.0.1 --reinstall-package orc $INSTALL_OPTS
$UV pip install orc_extras==0.0.1 --reinstall-package orc_extras $INSTALL_OPTS
$UV pip install orc_data==0.0.1 --reinstall-package orc_data $INSTALL_OPTS

if [ "$RESTART" ]; then
    supervisorctl start orc || true
fi

tail -f /var/log/orc.log
