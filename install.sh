. .venv-orc/bin/activate
supervisorctl stop orc
/root/.local/bin/uv pip install --no-cache --reinstall-package orc --index-url http://registry.int.exussum.org --extra-index-url https://pypi.org/simple/ --index-strategy unsafe-best-match --trusted-host registry.int.exussum.org orc
/root/.local/bin/uv pip install --no-cache --link-mode=copy --reinstall --no-deps --index-url http://registry.int.exussum.org --trusted-host registry.int.exussum.org orc
supervisorctl start orc
tail -f /var/log/orc.log
