scp src/config.md root@remote.exussum.org:/root/config.md
sh make.sh && ssh root@remote.exussum.org bash -s < install.sh
