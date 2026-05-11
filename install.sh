. .venv-orc/bin/activate
supervisorctl stop orc
pip uninstall orc --yes
pip install --index-url http://localhost:8080 orc
supervisorctl start orc
tail -f /var/log/orc.log
