. .venv-orc/bin/activate
supervisorctl stop orc
pip uninstall orc --yes
pip install --index-url http://registry.int.exussum.org --trusted-host registry.int.exussum.org orc
supervisorctl start orc
tail -f /var/log/orc.log
