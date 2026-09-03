#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-shot hardening for a fresh Ubuntu 24.04 VM that will run GauTrack.
# Run as root on the VM, BEFORE `docker compose up`:
#
#   sudo bash harden_vm.sh --admin-user gautrack --ssh-key "ssh-ed25519 AAAA..."
#
# What it does, and why:
#   ufw            firewall: only 22 (ssh), 80 and 443 (web) are reachable.
#   ssh key-only   passwords off, so a guessed password cannot get a shell.
#   fail2ban       bans an address that keeps failing ssh.
#   unattended-upgrades  installs security patches by itself.
#   docker         the container runtime the stack needs.
#   sysctl/limits  small kernel tweaks for a public-facing box.
# ---------------------------------------------------------------------------
set -euo pipefail

ADMIN_USER="gautrack"
SSH_KEY=""
SSH_PORT="22"

while [ $# -gt 0 ]; do
  case "$1" in
    --admin-user) ADMIN_USER="$2"; shift 2 ;;
    --ssh-key)    SSH_KEY="$2"; shift 2 ;;
    --ssh-port)   SSH_PORT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" = "0" ] || { echo "run as root" >&2; exit 1; }

echo "== packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ufw fail2ban unattended-upgrades ca-certificates curl gnupg \
                   postgresql-client age rsync chrony

echo "== admin user =="
id -u "$ADMIN_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$ADMIN_USER"
usermod -aG sudo "$ADMIN_USER"
if [ -n "$SSH_KEY" ]; then
  install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
  echo "$SSH_KEY" > "/home/$ADMIN_USER/.ssh/authorized_keys"
  chown "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.ssh/authorized_keys"
  chmod 600 "/home/$ADMIN_USER/.ssh/authorized_keys"
else
  echo "!! no --ssh-key given: make sure key access works BEFORE you log out."
fi

echo "== ssh: keys only =="
cat > /etc/ssh/sshd_config.d/99-gautrack.conf <<EOF
Port ${SSH_PORT}
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
X11Forwarding no
MaxAuthTries 3
ClientAliveInterval 300
AllowUsers ${ADMIN_USER}
EOF
systemctl reload ssh || systemctl reload sshd || true

echo "== firewall =="
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}"/tcp comment 'ssh'
ufw allow 80/tcp  comment 'http (redirects to https)'
ufw allow 443/tcp comment 'https'
ufw --force enable

echo "== fail2ban =="
cat > /etc/fail2ban/jail.d/gautrack.conf <<EOF
[sshd]
enabled = true
port    = ${SSH_PORT}
maxretry = 4
findtime = 10m
bantime  = 1h
EOF
systemctl enable --now fail2ban

echo "== unattended security upgrades =="
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
systemctl enable --now unattended-upgrades

echo "== docker =="
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
usermod -aG docker "$ADMIN_USER"
systemctl enable --now docker

echo "== kernel / limits =="
cat > /etc/sysctl.d/99-gautrack.conf <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
kernel.dmesg_restrict = 1
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
vm.swappiness = 10
EOF
sysctl --system >/dev/null

echo "== log rotation for the app stack =="
cat > /etc/logrotate.d/gautrack <<'EOF'
/var/lib/docker/volumes/*/_data/access.log {
  daily
  rotate 30
  compress
  missingok
  notifempty
  copytruncate
}
EOF

echo "== nightly backup cron (edit the path) =="
cat > /etc/cron.d/gautrack-backup <<EOF
30 2 * * * ${ADMIN_USER} cd /srv/gautrack/mvp && BACKUP_DIR=/srv/backups bash scripts/backup.sh >> /var/log/gautrack-backup.log 2>&1
15 3 * * * ${ADMIN_USER} cd /srv/gautrack/mvp && .venv/bin/python scripts/anchor.py >> /var/log/gautrack-anchor.log 2>&1
EOF

echo
echo "Done. Remaining manual steps:"
echo "  1. Confirm you can ssh in as $ADMIN_USER with your key, in a SECOND terminal."
echo "  2. Point the DNS name in SITE_ADDRESS at this machine so Caddy can get a certificate."
echo "  3. Copy the repo to /srv/gautrack, write mvp/.env, then: docker compose up -d --build"
echo "  4. Set SEED_DEMO=0 in .env for a real deployment."
