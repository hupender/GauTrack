#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Download the pinned front-end libraries ONCE, into api/static/vendor/, and
# record their SHA-256.  Nothing in the shipped HTML ever references a CDN
# (SPEC.md §1.2) — this script is a *build-time* step, run by a human.
#
#   scripts/vendor_libs.sh          download + write CHECKSUMS.txt
#   scripts/vendor_libs.sh verify   re-check the files already on disk
# ---------------------------------------------------------------------------
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$HERE/api/static/vendor"
mkdir -p "$VENDOR/images"

# name|url  — versions are pinned; bump deliberately, never with a floating tag.
FILES=(
  "htmx-2.0.4.min.js|https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js"
  "chart-4.4.7.umd.min.js|https://unpkg.com/chart.js@4.4.7/dist/chart.umd.js"
  "leaflet-1.9.4.js|https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  "leaflet-1.9.4.css|https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  "images/marker-icon.png|https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png"
  "images/marker-icon-2x.png|https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png"
  "images/marker-shadow.png|https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png"
  "images/layers.png|https://unpkg.com/leaflet@1.9.4/dist/images/layers.png"
  "images/layers-2x.png|https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png"
  "leaflet.markercluster-1.5.3.js|https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"
  "MarkerCluster-1.5.3.css|https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"
  "MarkerCluster.Default-1.5.3.css|https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"
)

sha() { shasum -a 256 "$1" | awk '{print $1}'; }

if [ "${1:-download}" = "verify" ]; then
  ( cd "$VENDOR" && shasum -a 256 -c <(grep -v '^#' CHECKSUMS.txt | grep -v '^$') )
  exit $?
fi

for entry in "${FILES[@]}"; do
  name="${entry%%|*}"; url="${entry#*|}"
  echo "[vendor] $name  <-  $url"
  curl -fsSL --retry 3 --max-time 120 -o "$VENDOR/$name" "$url"
done

{
  echo "# GauTrack vendored front-end libraries — SHA-256"
  echo "# Regenerate: scripts/vendor_libs.sh     Verify: scripts/vendor_libs.sh verify"
  echo "# Downloaded $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "#"
  echo "# source URLs:"
  for entry in "${FILES[@]}"; do
    echo "#   ${entry%%|*}  <-  ${entry#*|}"
  done
  echo ""
  for entry in "${FILES[@]}"; do
    name="${entry%%|*}"
    printf '%s  %s\n' "$(sha "$VENDOR/$name")" "$name"
  done
} > "$VENDOR/CHECKSUMS.txt"

echo "[vendor] wrote $VENDOR/CHECKSUMS.txt"
( cd "$VENDOR" && shasum -a 256 -c <(grep -v '^#' CHECKSUMS.txt | grep -v '^$') )
