. .venv-orc/bin/activate
pip uninstall orc --yes
pip install --index-url http://localhost:8080 orc
supervisorctl stop orc
supervisorctl start orc
tail -f /var/log/orc.log
