. .venv-orc/bin/activate
supervisorctl stop orc
/root/.local/bin/uv pip install --no-cache --link-mode=copy --reinstall-package orc --index-url http://registry.example.local --extra-index-url https://pypi.org/simple/ --index-strategy first-index --trusted-host registry.example.local orc==0.0.1
supervisorctl start orc
tail -f /var/log/orc.log
