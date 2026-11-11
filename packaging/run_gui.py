"""PyInstaller launcher for the GUI executable (windowed, no console)."""
import sys

from dwg2pdf.gui import main

if __name__ == "__main__":
    sys.exit(main())
