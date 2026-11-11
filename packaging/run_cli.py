"""PyInstaller launcher for the CLI executable (console)."""
import sys

from dwg2pdf.cli import main

if __name__ == "__main__":
    sys.exit(main())
