#!/usr/bin/env bash
# Print the URLs to open on a phone on the same Wi-Fi.
set -euo pipefail
ip=""
if command -v ipconfig >/dev/null 2>&1; then
  for i in en0 en1 en2 en3; do ip="$(ipconfig getifaddr "$i" 2>/dev/null || true)"; [ -n "$ip" ] && break; done
fi
[ -n "$ip" ] || ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$ip" ] || { echo "could not detect a LAN IP address" >&2; exit 1; }
cat <<TXT

  This laptop's LAN address: $ip

  On the phone (same Wi-Fi):
    field app     http://$ip:8000/app/      (works, but GPS is blocked on plain http)
    admin         http://$ip:8000/admin
    with HTTPS    https://$ip:8443/app/     (run 'make dev-tls' in another terminal; GPS works)

TXT
