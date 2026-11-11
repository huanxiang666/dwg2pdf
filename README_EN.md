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

## Development

```bash
git clone https://github.com/huanxiang666/dwg2pdf.git && cd dwg2pdf
pip install -e ".[dev]"
pytest -q
```

Release builds: push a `v*` tag — GitHub Actions builds Windows/Linux bundles (PyInstaller), smoke-tests them, and publishes a Release.

## License

**AGPL-3.0** (bundles [PyMuPDF](https://pymupdf.org/), AGPL). Rendering by [ezdxf](https://ezdxf.mozman.at/) (MIT). External converters (LibreDWG GPL-3, ODA File Converter freeware) are invoked as separate processes and keep their own licenses. See [LICENSE](LICENSE).
