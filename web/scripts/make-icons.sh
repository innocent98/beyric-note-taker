#!/usr/bin/env bash
# Regenerates public/icons/*.png from the logo (app/icon.svg). Needs rsvg-convert (brew install
# librsvg). The PNGs are committed, so this only runs when the logo changes.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Rounded tile, as the favicon.
cp app/icon.svg "$tmp/rounded.svg"
# Full-bleed tile with the glyph inside the maskable safe zone (centre 80%), for maskable and
# Apple icons, which the system crops itself.
cat > "$tmp/bleed.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><rect width="32" height="32" fill="#0E6B5E"/><g transform="translate(4.8 4.8) scale(0.7)"><path d="M9 8H23a2 2 0 0 1 2 2V19a2 2 0 0 1-2 2H15L10.5 24.5V21H9a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2Z" fill="#FFFFFF"/><path d="M11 12.5h10M11 16.5h6" stroke="#0E6B5E" stroke-width="1.6" stroke-linecap="round"/></g></svg>
SVG

mkdir -p public/icons
rsvg-convert -w 192 -h 192 "$tmp/rounded.svg" -o public/icons/icon-192.png
rsvg-convert -w 512 -h 512 "$tmp/rounded.svg" -o public/icons/icon-512.png
rsvg-convert -w 512 -h 512 "$tmp/bleed.svg" -o public/icons/maskable-512.png
rsvg-convert -w 180 -h 180 "$tmp/bleed.svg" -o public/icons/apple-touch-icon.png
