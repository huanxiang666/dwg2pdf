# dwg2pdf — Batch DWG/DXF to PDF Converter

[![CI](https://github.com/huanxiang666/dwg2pdf/actions/workflows/ci.yml/badge.svg)](https://github.com/huanxiang666/dwg2pdf/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

Cross-platform (**Windows / Linux**) batch converter that turns CAD drawings (**DWG / DXF**) into **vector PDFs**, with both a CLI and a GUI.

中文文档：[README.md](README.md)

## Highlights

- **Batch conversion** — multiple files, whole directories (recursive), parallel workers; one bad file never kills the batch
- **Vector PDF output** via ezdxf + PyMuPDF — infinitely zoomable, print/archive ready
- **Respects CAD plot semantics** — paperspace layouts export at their own paper size and plot scale; modelspace auto-fits a page (capped at ISO A0); multiple layouts merge into one multi-page PDF
- **Options** — monochrome, fixed paper sizes (ISO/ANSI/ARCH/Letter/Legal), scale, margins, skip-existing
- **CLI + GUI** — executables ship both (`dwg2pdf` / `dwg2pdf-gui`)
- **Honest failures** — JSON batch report, meaningful exit codes, explicit WARN + blank page for degenerate geometry instead of silent garbage
- **Battle-tested** — 60 real-world DWG files from the LibreDWG corpus convert at 100%

## Install

**Binaries (recommended):** grab `dwg2pdf-x.y.z-windows-x64.zip` or `dwg2pdf-x.y.z-linux-x64.tar.gz` from [Releases](https://github.com/huanxiang666/dwg2pdf/releases), unpack, run `dwg2pdf-gui(.exe)` or `dwg2pdf(.exe)`.

**pip (Python 3.10+):**

```bash
pip install git+https://github.com/huanxiang666/dwg2pdf.git
```

## DWG support: one free converter required

**DXF works out of the box.** DWG (proprietary) is converted through either free external tool, auto-detected (or force via `DWG2PDF_CONVERTER=/path/to/exe`):

1. **ODA File Converter** — <https://www.opendesign.com/guestfiles/oda_file_converter>
2. **GNU LibreDWG `dwg2dxf`** — `sudo apt install libredwg-tools` (Debian/Ubuntu) or build from <https://www.gnu.org/software/libredwg/>

Run `dwg2pdf --check` to verify your environment.

## v0.3.0 highlights

- **CAD-viewer-style GUI**: ttkbootstrap theme, three-pane layout (files | preview canvas | options), live status bar with ETA + CPU%.
- **Drawing preview**: wheel-zoom / right-drag-pan raster preview via a custom Pillow replay backend (zero new dependencies). 57 MB drawing: preview in 15 s.
- **Region export**: rubber-band a rectangle on the preview → export exactly that window as a vector PDF → print / open / reveal. Crop-based (34k records → 4k), 6–8 s with a single-entry DXF cache.
- **Engine Assistant**: in-app multi-source download of the LibreDWG engine (official GitHub + 3 mirrors + optional Go fast core) — parallel speed test picks the fastest (measured 5x spread), 4-thread HTTP Range download with resume, zip verification, auto-install into the user engine dir.
- **Manual update check**: GitHub Releases query filtered to this GUI release train by asset naming (never confuses the separate Go CLI train), shows changelog + download page; never phones home automatically.
- **Go fast core (experimental)**: optional renderer from this repo's second release train; any failure falls back to the built-in renderer with a warning.

## v0.2.0 highlights

- **Installers**: Windows NSIS setup (.exe, silent `/S` supported), Linux `.deb` (bundles the LibreDWG DWG engine — DWG works out of the box), plus portable zip/tar.gz and an experimental macOS arm64 build. All assets ship with sha256 checksums.
- **Fonts that just work**: bundled Apache-2.0 CJK font substitutes ~30 common missing CAD font names (simsun, hztxt.shx, gbcbig.shx, ...); real fonts always win. Import your own font libraries via `--font-dir` (TTF/SHX/SHP/LFF, recursive).
- **Quality presets**: `--quality fast|balanced|high` (curve fidelity vs speed/size).
- **Real multithreading**: `-j N` across files, `--threads N` for chunk-parallel rendering of one big drawing (measured 189s → 83s on 8 cores, byte-identical geometry). No fake GPU switch: vector PDF generation is CPU-bound; `--check` reports this honestly.

## Usage

```bash
dwg2pdf drawing.dwg                       # PDF next to the source
dwg2pdf ./drawings -r -o ./pdf            # whole tree into ./pdf
dwg2pdf *.dwg --monochrome --paper a4 -j 8
dwg2pdf project.dwg --layout all --report report.json
dwg2pdf --gui                             # graphical interface
dwg2pdf --check                           # environment diagnostics
```

Exit codes: `0` all ok · `1` some failed · `2` usage error. Full flag table in the [Chinese README](README.md#参数一览).

## Performance expectations (measured)

Render cost scales with vector-path count. Reference on a 2020-class desktop (single core):

| Drawing size | Time |
|---|---|
| Normal (< 5 MB DXF) | seconds |
| Large (57 MB DXF, ~14k entities + ~5.7k inserts) | ~3 min for modelspace |
| Same file, paperspace layout with 3 viewports | ~23 min (each viewport re-renders the full model) |

- For big drawings prefer `--layout modelspace` — usually an order of magnitude faster than multi-viewport layout pages.
- The CLI prints a `[heartbeat]` line every 30 s and the GUI shows live elapsed time — a long-running file is working, not hung.
- An intermediate DXF above `--large-dxf-mb` (default 5) triggers a "large drawing" warning.

## Development

```bash
git clone https://github.com/huanxiang666/dwg2pdf.git && cd dwg2pdf
pip install -e ".[dev]"
pytest -q
```

Release builds: push a `v*` tag — GitHub Actions builds Windows/Linux bundles (PyInstaller), smoke-tests them, and publishes a Release.

## License

**AGPL-3.0** (bundles [PyMuPDF](https://pymupdf.org/), AGPL). Rendering by [ezdxf](https://ezdxf.mozman.at/) (MIT). External converters (LibreDWG GPL-3, ODA File Converter freeware) are invoked as separate processes and keep their own licenses. See [LICENSE](LICENSE).
