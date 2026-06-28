sh scripts/upload.sh "$1" && ssh root@deploy.example.local bash -s < scripts/install.sh

