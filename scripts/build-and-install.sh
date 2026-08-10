if [ ! -f scripts/deploy.env ]; then
    echo "scripts/deploy.env missing — copy scripts/deploy.env.example and set your hosts" >&2
    exit 1
fi
. scripts/deploy.env
DEPLOY="${ORC_DEPLOY_USER:-root}@$ORC_DEPLOY_HOST"
CTRL="-o ControlMaster=auto -o ControlPath=/tmp/orc-ssh-%r@%h:%p -o ControlPersist=60"
sh scripts/upload.sh "$1" \
    && scp $CTRL pyproject.toml "$DEPLOY:/tmp/pyproject.toml" \
    && ssh $CTRL "$DEPLOY" "ORC_REGISTRY_URL='$ORC_REGISTRY_URL' ORC_TRUSTED_HOST='$ORC_TRUSTED_HOST' bash -s" < scripts/install.sh

