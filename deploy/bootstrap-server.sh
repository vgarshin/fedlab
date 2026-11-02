#!/usr/bin/env bash
# One-time setup of a clean Ubuntu 24.04 VM (fedlab-01). Run as the normal sudo user, NOT root:
#   bash bootstrap-server.sh
# Idempotent. Installs Docker Engine + compose plugin, unattended security updates, fail2ban,
# ufw (22/80/443), 2 GB swap (the VM has ~4 GB RAM), key-only SSH, and creates /opt/fedlab.
set -euo pipefail

if [[ $EUID -eq 0 ]]; then echo "Run as a normal user with sudo, not root." >&2; exit 1; fi
. /etc/os-release
[[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.04" ]] || { echo "Expected Ubuntu 24.04, got: $PRETTY_NAME" >&2; exit 1; }

echo "== 1/7 Packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg rsync python3 unattended-upgrades fail2ban ufw

echo "== 2/7 Docker Engine (official repository)"
if ! command -v docker >/dev/null 2>&1; then
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo "== 3/7 Docker log rotation"
sudo mkdir -p /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
    echo '{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}}' | sudo tee /etc/docker/daemon.json >/dev/null
    sudo systemctl restart docker
fi

echo "== 4/7 Automatic security updates"
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== 5/7 Swap 2 GB"
if ! swapon --show | grep -q .; then
    sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== 6/7 Firewall: 22, 80, 443 (the cloud security group must allow the same)"
sudo ufw default deny incoming >/dev/null
sudo ufw default allow outgoing >/dev/null
for p in 22 80 443; do sudo ufw allow "$p"/tcp >/dev/null; done
sudo ufw --force enable >/dev/null
sudo systemctl enable --now fail2ban >/dev/null 2>&1 || true

echo "== 7/7 SSH hardening + project directory"
if [[ -s "$HOME/.ssh/authorized_keys" ]]; then
    printf 'PasswordAuthentication no\nPermitRootLogin no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null
    sudo sshd -t && sudo systemctl reload ssh
else
    echo "SKIPPED SSH hardening: no ~/.ssh/authorized_keys (would lock you out)" >&2
fi
sudo mkdir -p /opt/fedlab && sudo chown "$USER":"$USER" /opt/fedlab
echo "Done. Log out and in again so the 'docker' group applies. Next: deploy/deploy.sh from your workstation."
