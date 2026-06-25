curl -sSL https://raw.githubusercontent.com/exussum/orc/main/pyproject.toml -o /tmp/pyproject.toml
UV_PROJECT_ENVIRONMENT=/root/.venv-orc UV_LINK_MODE=copy UV_TRUSTED_HOST=registry.int.exussum.org /root/.local/bin/uv sync --no-install-project --no-cache --directory /tmp
supervisorctl stop orc
VIRTUAL_ENV=/root/.venv-orc /root/.local/bin/uv pip install orc==0.0.1 --no-deps --reinstall-package orc --link-mode=copy --index-url http://registry.int.exussum.org --trusted-host registry.int.exussum.org --no-cache
supervisorctl start orc
tail -f /var/log/orc.log
