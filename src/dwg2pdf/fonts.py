"""Font management: bundled CJK font, missing-font substitution, user font dirs.

Goals (user-facing promises):
  1. Works out of the box — a CJK-capable font (Droid Sans Fallback Full,
     Apache-2.0) ships inside the package, so Chinese text renders even on a
     fresh machine with no CAD fonts installed.
  2. Missing fonts are substituted — common Chinese CAD font names (simsun,
     simhei, hztxt.shx, gbcbig.shx, ...) are registered as aliases of the
     bundled font WHEN the real font is absent. A real font already on the
     system (or supplied by the user) always wins; we never shadow it.
  3. Users can import their own font libraries — every --font-dir / GUI
     directory is scanned recursively for TTF/OTF/TTC/SHX/SHP/LFF and takes
     precedence over the bundled substitute (registered before aliasing).

Implementation notes:
  ezdxf keeps ONE global FontManager (ezdxf.fonts.fonts.font_manager) built
  from a stale-able XDG cache. We force a fresh scan (system dirs + bundled +
  user dirs) once per process and then register aliases via
  FontManager.add_synonyms({present_font: missing_alias}, reverse=False) —
  note the installed ezdxf semantics: the KEY must already be in cache, the
  VALUE is the alias being added.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Bundled font file name and its lowercase FontCache key.
BUNDLED_FONT_NAME = "DroidSansFallbackFull.ttf"
BUNDLED_CACHE_KEY = BUNDLED_FONT_NAME.lower()

#: Common Chinese CAD font names substituted by the bundled font when missing.
CJK_TTF_ALIASES: tuple[str, ...] = (
    "simsun.ttf", "simsun.ttc", "nsimsun.ttf", "simhei.ttf", "simkai.ttf",
    "simfang.ttf", "msyh.ttf", "msyh.ttc", "msyhbd.ttf", "deng.ttf",
    "dengxian.ttf", "simyou.ttf",
)
CJK_SHX_ALIASES: tuple[str, ...] = (
    "hztxt.shx", "gbcbig.shx", "gbcbigt.shx", "gbenor.shx", "hzst.shx",
    "hzfs.shx", "tssdeng.shx", "tssdchn.shx", "fs.shx", "st.shx", "ht.shx",
    "kt.shx", "song.shx", "china.shx", "chineset.shx", "bigfont.shx",
    "hzdz.shx", "hzst2.shx",
)
CJK_ALIASES: tuple[str, ...] = CJK_TTF_ALIASES + CJK_SHX_ALIASES

_setup_cache: dict[tuple[str, ...], dict] = {}


def bundled_font_dir() -> Path | None:
    """Directory holding the bundled font (source tree or PyInstaller bundle)."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "dwg2pdf" / "font_data")
    candidates.append(Path(__file__).resolve().parent / "font_data")
    for cand in candidates:
        if (cand / BUNDLED_FONT_NAME).is_file():
            return cand
    return None


def setup(user_dirs: tuple[str, ...] | list[str] | None = ()) -> dict:
    """(Re)build the ezdxf font manager and register CJK substitutions.

    Idempotent per-process for the same set of user dirs. Returns a dict for
    diagnostics: bundled font path, registered aliases, effective dirs.
    """
    key = tuple(str(d) for d in (user_dirs or ()))
    if key in _setup_cache:
        return _setup_cache[key]

    from ezdxf.fonts import font_manager as ez_fm
    from ezdxf.fonts import fonts as ez_fonts

    fm = ez_fonts.font_manager
    info: dict = {
        "bundled_font": None,
        "aliases_registered": [],
        "user_dirs": [],
        "font_count": 0,
    }

    folders: list[str] = []
    bundled = bundled_font_dir()
    if bundled is not None:
        folders.append(str(bundled))
        info["bundled_font"] = str(bundled / BUNDLED_FONT_NAME)
    for d in key:
        if d and Path(d).is_dir():
            folders.append(d)
            info["user_dirs"].append(d)
    # Keep platform system dirs (Windows Fonts / /usr/share/fonts / ...) so
    # real installed CAD fonts are found and win over our substitutes.
    try:
        sysdirs = ez_fm.FONT_DIRECTORIES.get(fm.platform, ez_fm.LINUX_FONT_DIRS)
    except Exception:
        sysdirs = ez_fm.LINUX_FONT_DIRS
    folders.extend(str(d) for d in sysdirs)

    # Force a fresh scan (bypasses the possibly stale XDG cache).
    fm.build(folders=folders, support_dirs=True)
    info["font_count"] = len(fm._font_cache)

    if bundled is not None and BUNDLED_CACHE_KEY in fm._font_cache:
        for alias in CJK_ALIASES:
            if alias in fm._font_cache:
                continue  # the real font exists — never shadow it
            try:
                fm.add_synonyms({BUNDLED_CACHE_KEY: alias}, reverse=False)
            except Exception:
                continue
            if alias in fm._font_cache:
                info["aliases_registered"].append(alias)

    _setup_cache[key] = info
    return info
