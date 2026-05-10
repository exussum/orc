#!/bin/tcsh
source /Users/spence/.venv-orc/bin/activate.csh
setenv ENABLED 1
setenv BASE_URL http://hub.exussum.org/apps/api/37
setenv BWS_ACCESS_TOKEN "data:text/plain;base64,MC5iNjAxOTMyYS04MTFjLTRkZDMtYWE3Zi1iNDNiMDAyYjE2NzQuOFd1ODY2eGNDM2xKeEgza3BKWlJ3UEdLWjQ5ZEZxOjZOdU9NczhkQnlDL2VMNEFJN1hyU1E9PQ=="
setenv PYTHONPATH src:data/src
setenv BWS_ORG_ID "data:text/plain;base64,MTZiMzI4MTMtOWExNy00NGEzLWExYmYtYjQzODAwZmVkZTM2"
python -c "from orc.runner import web; web()"
