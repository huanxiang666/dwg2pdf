#!/usr/bin/env bash
# Build a .deb package from a PyInstaller onedir bundle.
# Usage: make_deb.sh <version> <dist-dir> <output.deb>
# Runs without root (dpkg-deb -b --root-owner-group).
set -euo pipefail

VER="${1:?usage: make_deb.sh VERSION DIST_DIR OUT.deb}"
DIST="${2:?missing dist dir (e.g. dist/dwg2pdf)}"
OUT="${3:?missing output .deb path}"

[ -d "$DIST" ] || { echo "dist dir not found: $DIST" >&2; exit 1; }
[ -x "$DIST/dwg2pdf" ] || { echo "$DIST/dwg2pdf missing or not executable" >&2; exit 1; }

WORK="$(mktemp -d /tmp/dwg2pdf-deb.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/dwg2pdf_${VER}_amd64"

mkdir -p "$ROOT/DEBIAN" \
         "$ROOT/opt/dwg2pdf" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/256x256/apps" \
         "$ROOT/usr/share/doc/dwg2pdf"

cp -a "$DIST/." "$ROOT/opt/dwg2pdf/"
ln -s /opt/dwg2pdf/dwg2pdf      "$ROOT/usr/bin/dwg2pdf"
ln -s /opt/dwg2pdf/dwg2pdf-gui  "$ROOT/usr/bin/dwg2pdf-gui"

cat > "$ROOT/DEBIAN/control" <<EOF
Package: dwg2pdf
Version: ${VER}
Section: graphics
Priority: optional
Architecture: amd64
Depends: libc6 (>= 2.35)
Maintainer: huanxiang666 <huanxiang666@users.noreply.github.com>
Homepage: https://github.com/huanxiang666/dwg2pdf
Description: Batch DWG/DXF to vector PDF converter (CLI + GUI)
 Converts CAD drawings (DWG via bundled LibreDWG engine, DXF natively)
 to searchable-grade vector PDFs. Batch directories in parallel, honors
 CAD plot semantics (paperspace page size/scale), monochrome output,
 named paper sizes, page-level progress. Bundles a CJK font so Chinese
 text renders out of the box; user font libraries via --font-dir.
 .
 Commands: dwg2pdf (CLI), dwg2pdf-gui (graphical interface).
EOF

cat > "$ROOT/usr/share/applications/dwg2pdf.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=dwg2pdf
GenericName=CAD to PDF Converter
Comment=Batch convert DWG/DXF drawings to vector PDF
Exec=dwg2pdf-gui
Icon=dwg2pdf
Terminal=false
Categories=Graphics;Engineering;Office;
Keywords=cad;dwg;dxf;pdf;convert;图纸;转换;
EOF

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/icon.png" ]; then
  cp "$SCRIPT_DIR/icon.png" "$ROOT/usr/share/icons/hicolor/256x256/apps/dwg2pdf.png"
fi
for f in README.md LICENSE THIRD-PARTY-NOTICES.md; do
  [ -f "$SCRIPT_DIR/../$f" ] && cp "$SCRIPT_DIR/../$f" "$ROOT/usr/share/doc/dwg2pdf/" || true
done

dpkg-deb --build --root-owner-group "$ROOT" "$OUT" >/dev/null
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
