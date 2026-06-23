. .venv-orc/bin/activate
supervisorctl stop orc
/root/.local/bin/uv pip install --no-cache --reinstall --no-deps --index-url http://registry.example.local --trusted-host registry.example.local orc
supervisorctl start orc
tail -f /var/log/orc.log
