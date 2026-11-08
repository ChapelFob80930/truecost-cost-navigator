#!/usr/bin/env bash
# TrueCost one-shot server setup for a fresh Ubuntu 22.04/24.04 Vultr instance.
#   sudo bash /opt/truecost/deploy/setup.sh api.yourdomain.com
# Before running: the project is unzipped in /opt/truecost and /opt/truecost/.env exists (see DEPLOY.md),
# and the domain's DNS A record already points at this server (Caddy needs that to get the https certificate).
set -euo pipefail

DOMAIN="${1:?usage: sudo bash setup.sh your.domain.com   (or IP-WITH-DASHES.sslip.io)}"
APP=/opt/truecost
[ "$(id -u)" -eq 0 ] || { echo "Run as root (use sudo)."; exit 1; }
[ -d "$APP/backend/app" ] || { echo "Missing $APP/backend. Unzip the bundle into $APP first."; exit 1; }
[ -f "$APP/.env" ] || { echo "Missing $APP/.env. Create it from .env.example first."; exit 1; }

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip ufw curl gpg debian-keyring debian-archive-keyring apt-transport-https

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> Installing Caddy (official apt repository)"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

echo "==> Creating service user and Python environment"
id truecost >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin truecost
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r "$APP/backend/requirements.txt"
chown -R truecost:truecost "$APP"
chmod 600 "$APP/.env"                       # holds the Gemini key and the database password

echo "==> Installing the API service"
install -m 644 "$APP/deploy/truecost.service" /etc/systemd/system/truecost.service
systemctl daemon-reload
systemctl enable --now truecost

echo "==> Configuring https reverse proxy for $DOMAIN"
sed "s/__DOMAIN__/$DOMAIN/g" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl enable caddy
systemctl restart caddy

echo "==> Firewall (SSH, http, https only)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Waiting for the API"
for i in $(seq 1 30); do
  if curl -fs http://127.0.0.1:8010/health >/dev/null; then break; fi
  sleep 1
done
curl -s http://127.0.0.1:8010/health || { echo; echo "API did not start. Run: journalctl -u truecost -n 50 --no-pager"; exit 1; }
echo
echo "Done. Try:  curl https://$DOMAIN/health      (should show \"db\":\"postgres\" when DATABASE_URL is set)"
echo "Then run:   bash $APP/deploy/check.sh https://$DOMAIN"
