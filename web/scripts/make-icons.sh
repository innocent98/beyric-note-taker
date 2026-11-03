#!/usr/bin/env bash
# Regenerates the icons from the designer's logo files. Needs rsvg-convert (brew install librsvg)
# and sips (macOS). The results are committed, so this only runs when the logo changes.
#
#   public/brand/*.png      the designer's exports (docs/ui/Beyric Note Taker redesign/logo/), copied as is
#   app/icon.svg            favicon: the mark at 16px and below (no centre dot, heavier stroke, Brand.dc.html)
#   public/icons/icon-*.png the rounded tile, resized from the 1024 export
#   maskable and Apple      full-bleed tile with the glyph inside the safe zone (the system crops them)
#   public/og.png           1200x630 share image: the wide logo on the Paper background
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
src="../docs/ui/Beyric Note Taker redesign/logo"

mkdir -p public/brand public/icons
cp "$src"/*.png public/brand/

sips -z 192 192 public/brand/beyric-note-taker-icon-1024.png --out public/icons/icon-192.png >/dev/null
sips -z 512 512 public/brand/beyric-note-taker-icon-1024.png --out public/icons/icon-512.png >/dev/null

cat > "$tmp/bleed.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120"><rect width="120" height="120" fill="#0E6B5E"/><g transform="translate(18 18) scale(0.7)"><path d="M38 24V72A24 24 0 1 0 62 48" fill="none" stroke="#FFFFFF" stroke-width="14" stroke-linecap="round"/><circle cx="62" cy="72" r="6" fill="#FFFFFF"/></g></svg>
SVG
rsvg-convert -w 512 -h 512 "$tmp/bleed.svg" -o public/icons/maskable-512.png
rsvg-convert -w 180 -h 180 "$tmp/bleed.svg" -o public/icons/apple-touch-icon.png

logo64="$(base64 < public/brand/beyric-note-taker-logo.png | tr -d '\n')"
cat > "$tmp/og.svg" <<SVG
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1200" height="630" viewBox="0 0 1200 630"><rect width="1200" height="630" fill="#F6F7F6"/><image x="150" y="187" width="900" height="241" xlink:href="data:image/png;base64,$logo64"/></svg>
SVG
rsvg-convert "$tmp/og.svg" -o public/og.png
echo "icons written"
