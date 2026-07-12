if [ ! -f scripts/deploy.env ]; then
    echo "scripts/deploy.env missing — copy scripts/deploy.env.example and set your hosts" >&2
    exit 1
fi
. scripts/deploy.env
sh scripts/upload.sh "$1" && ssh "root@$ORC_DEPLOY_HOST" "ORC_REGISTRY_URL='$ORC_REGISTRY_URL' ORC_TRUSTED_HOST='$ORC_TRUSTED_HOST' bash -s" < scripts/install.sh

