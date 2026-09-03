#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# HTTPS in front of the dev server, for demoing the field PWA on a real phone
# over the LAN.
#
#   make dev      # terminal 1 — the API on 0.0.0.0:8000
#   make dev-tls  # terminal 2 — Caddy on https://<lan-ip>:8443
#
# Android Chrome refuses geolocation, camera capture and service workers on a
# plain-http LAN address. Caddy's `tls internal` issues a certificate from a
# local certificate authority, which fixes that once the phone trusts the CA.
# ---------------------------------------------------------------------------
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v caddy >/dev/null 2>&1 || {
  echo "caddy is not installed.  brew install caddy   (or: docker compose up caddy)" >&2
  exit 1
}

lan_ip() {
  # first non-loopback IPv4 on the active interface
  if command -v ipconfig >/dev/null 2>&1; then
    for i in en0 en1 en2 en3; do
      ip="$(ipconfig getifaddr "$i" 2>/dev/null || true)"
      [ -n "$ip" ] && { echo "$ip"; return; }
    done
  fi
  hostname -I 2>/dev/null | awk '{print $1}'
}

GT_LAN_IP="${GT_LAN_IP:-$(lan_ip)}"
[ -n "$GT_LAN_IP" ] || { echo "could not work out this machine's LAN IP; set GT_LAN_IP=..." >&2; exit 1; }

export GT_LAN_IP
export GT_CADDY_DATA="$HERE/.caddy"
mkdir -p "$GT_CADDY_DATA"

cat <<EOF

  Field PWA over HTTPS:   https://${GT_LAN_IP}:8443/app/
  Admin dashboard:        https://${GT_LAN_IP}:8443/admin
  CM view:                https://${GT_LAN_IP}:8443/cm

  On the phone (same Wi-Fi), Chrome will warn that the certificate is not
  trusted. Either:
    a) tap "Advanced" -> "Proceed" — enough for a quick demo, but Chrome still
       treats it as insecure, so GPS stays blocked; or
    b) trust the demo certificate authority properly, which makes GPS work:
         1. on the phone open  http://${GT_LAN_IP}:8000/static/rootca.crt
         2. Settings -> Security -> Encryption & credentials ->
            Install a certificate -> CA certificate -> pick the downloaded file
         3. reload https://${GT_LAN_IP}:8443/app/

  Ctrl-C to stop.

EOF

# publish the local CA where the phone can fetch it over plain http
ROOT_CA="$GT_CADDY_DATA/pki/authorities/local/root.crt"
( for _ in 1 2 3 4 5 6 7 8; do [ -f "$ROOT_CA" ] && break; sleep 1; done; [ -f "$ROOT_CA" ] && cp "$ROOT_CA" "$HERE/api/static/rootca.crt" && \
  echo "[dev_tls] root CA published at http://${GT_LAN_IP}:8000/static/rootca.crt" ) &

exec caddy run --config "$HERE/Caddyfile.dev" --adapter caddyfile
