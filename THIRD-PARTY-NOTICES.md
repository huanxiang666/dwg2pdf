# Third-Party Notices

dwg2pdf bundles or invokes the following third-party components. This file is
provided for license compliance; each component remains under its own license.

## Bundled Python libraries (shipped inside the frozen executables)

- **ezdxf** — DXF/DWG document model and the drawing add-on used for rendering.
  Copyright (c) Manfred Moitzi. License: MIT.
  https://github.com/mozman/ezdxf

- **PyMuPDF** — PDF backend that produces the vector output.
  Copyright (C) Artifex Software, Inc. License: AGPL-3.0 (a commercial license
  is available from Artifex). https://pymupdf.org/
  Because dwg2pdf bundles PyMuPDF, the combined frozen executables are
  distributed under the AGPL-3.0 (see LICENSE).

- **Pillow (PIL)** — image handling required by ezdxf's drawing frontend.
  License: HPND (MIT-CMU style). https://python-pillow.org/

- **Python** standard library and the CPython runtime embedded by PyInstaller.
  License: PSF License Agreement. https://docs.python.org/3/license.html

- **Tk/Tcl** — the Tkinter GUI runtime, embedded by PyInstaller on some
  platforms. License: BSD-style. https://www.tcl.tk/software/tcltk/license.html

## Optional external converters (invoked as separate processes, NOT bundled)

These are discovered at runtime and executed as child processes. They are not
redistributed by dwg2pdf, so their licenses do not affect this project's license.

- **GNU LibreDWG** (`dwg2dxf`) — DWG → DXF conversion.
  License: GPL-3.0-or-later. https://www.gnu.org/software/libredwg/

- **ODA File Converter** (Open Design Alliance) — DWG → DXF conversion.
  Distributed by ODA as freeware under its own terms.
  https://www.opendesign.com/guestfiles/oda_file_converter

## Packaging tooling (build time only, not shipped)

- **PyInstaller** — builds the standalone executables. License: GPL-2.0-or-later
  with a special exception allowing bundling of any library under its own terms;
  the bootloader exception keeps the produced executables under dwg2pdf's license.
  https://pyinstaller.org/
