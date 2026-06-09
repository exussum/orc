. .venv-orc/bin/activate
supervisorctl stop orc
pip uninstall orc --yes
pip install --index-url http://registry.example.local --trusted-host registry.example.local orc
supervisorctl start orc
tail -f /var/log/orc.log
