#!/usr/bin/env python3
"""Genera le icone PWA raster da static/pwa/*.svg.

Uso (dal root del progetto, con cairosvg installato):
    python scripts/generate_pwa_icons.py

Produce in static/pwa/:
  - badge-96.png            silhouette monocromatica trasparente (badge notifiche Android)
  - icon-maskable-192.png   glifo con safe-zone su plate pieno (icona adattiva Android)
  - icon-maskable-512.png
  - icon-192.png / icon-512.png / apple-touch-icon.png   raster di icon.svg

I colori del brand (plate e glifo) vengono letti da icon.svg: per cambiare
palette modifica gli SVG e rilancia lo script. Le PNG del manifest sono
servite con nomi hashati (ManifestStaticFilesStorage via static()), quindi
possono essere rigenerate in place senza problemi di cache.
"""
import re
from pathlib import Path

import cairosvg

PWA = Path(__file__).resolve().parent.parent / 'static' / 'pwa'

SKULL_PATH = re.search(r'<path d="([^"]+)"', (PWA / 'icon.svg').read_text()).group(1)
PLATE = re.search(r'<rect[^>]*fill="([^"]+)"', (PWA / 'icon.svg').read_text()).group(1)
GLYPH = re.search(r'<g fill="([^"]+)"', (PWA / 'icon.svg').read_text()).group(1)

# Icona maskable: plate a tutto sangue (l'OS applica la maschera) e glifo
# ridotto al 68% per stare nella safe-zone (cerchio con raggio 40% del lato).
MASKABLE_SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" fill="{PLATE}"/>
  <g fill="{GLYPH}" transform="translate(256,242) scale(0.68) translate(-256,-256)">
    <path d="{SKULL_PATH}"/>
  </g>
</svg>'''


def render(svg: bytes | str, out: Path, size: int) -> None:
    data = svg.encode() if isinstance(svg, str) else svg
    cairosvg.svg2png(bytestring=data, write_to=str(out), output_width=size, output_height=size)
    print(f'  {out.name} ({size}x{size})')


def main() -> None:
    icon_svg = (PWA / 'icon.svg').read_bytes()
    badge_svg = (PWA / 'badge.svg').read_bytes()
    render(badge_svg, PWA / 'badge-96.png', 96)
    render(MASKABLE_SVG, PWA / 'icon-maskable-192.png', 192)
    render(MASKABLE_SVG, PWA / 'icon-maskable-512.png', 512)
    render(icon_svg, PWA / 'icon-192.png', 192)
    render(icon_svg, PWA / 'icon-512.png', 512)
    render(icon_svg, PWA / 'apple-touch-icon.png', 180)


if __name__ == '__main__':
    main()
