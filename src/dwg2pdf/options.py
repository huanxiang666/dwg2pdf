"""Conversion options shared by CLI, GUI and batch workers.

Everything in :class:`ConvertOptions` must stay picklable (plain values only)
because it is sent to ProcessPoolExecutor workers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class OptionError(ValueError):
    """Raised when a user-supplied option value is invalid."""


# Canonical paper names come from ezdxf's PAGE_SIZES; we add lowercase aliases
# so users can type "a4" instead of "ISO A4".
_PAPER_ALIASES = {
    "a0": "ISO A0",
    "a1": "ISO A1",
    "a2": "ISO A2",
    "a3": "ISO A3",
    "a4": "ISO A4",
    "ansi-a": "ANSI A",
    "ansi a": "ANSI A",
    "ansi-b": "ANSI B",
    "ansi b": "ANSI B",
    "ansi-c": "ANSI C",
    "ansi c": "ANSI C",
    "ansi-d": "ANSI D",
    "ansi d": "ANSI D",
    "ansi-e": "ANSI E",
    "ansi e": "ANSI E",
    "arch-c": "ARCH C",
    "arch c": "ARCH C",
    "arch-d": "ARCH D",
    "arch d": "ARCH D",
    "arch-e": "ARCH E",
    "arch e": "ARCH E",
    "arch-e1": "ARCH E1",
    "arch e1": "ARCH E1",
    "letter": "Letter",
    "legal": "Legal",
}

#: Paper choices accepted by the CLI/GUI ("auto" plus every canonical name).
PAPER_CHOICES: tuple[str, ...] = (
    "auto",
    "ISO A0",
    "ISO A1",
    "ISO A2",
    "ISO A3",
    "ISO A4",
    "ANSI A",
    "ANSI B",
    "ANSI C",
    "ANSI D",
    "ANSI E",
    "ARCH C",
    "ARCH D",
    "ARCH E",
    "ARCH E1",
    "Letter",
    "Legal",
)

LAYOUT_CHOICES = ("auto", "modelspace", "all")

#: Quality/efficiency presets. Each maps to ezdxf drawing Configuration
#: overrides; "balanced" is the ezdxf default. Higher quality = more curve
#: segments + finer flattening = bigger output and slower render; "fast"
#: trades a little curve smoothness for a large speed/size win on drawings
#: with many arcs/circles/splines.
QUALITY_PRESETS: dict[str, dict] = {
    "fast": {"circle_approximation_count": 36, "max_flattening_distance": 0.5},
    "balanced": {"circle_approximation_count": 128, "max_flattening_distance": 0.01},
    "high": {"circle_approximation_count": 512, "max_flattening_distance": 0.001},
}
QUALITY_CHOICES = ("fast", "balanced", "high")


def resolve_paper(name: str) -> str:
    """Normalize a user paper name to a canonical ezdxf PAGE_SIZES key.

    Returns "auto" unchanged. Raises OptionError for unknown names.
    """
    if name is None:
        return "auto"
    key = name.strip()
    if key.lower() == "auto":
        return "auto"
    lowered = key.lower()
    if lowered in _PAPER_ALIASES:
        return _PAPER_ALIASES[lowered]
    for canonical in PAPER_CHOICES[1:]:
        if canonical.lower() == lowered:
            return canonical
    raise OptionError(
        f"unknown paper size: {name!r} (expected one of: auto, A0-A4, ANSI A-E, ARCH C-E, Letter, Legal)"
    )


@dataclass(frozen=True)
class ConvertOptions:
    """One immutable description of how to convert files.

    Attributes:
        layout: which layout(s) of each drawing to export —
            "auto" (paperspace layouts that contain entities, else modelspace),
            "modelspace", "all" (modelspace + every paperspace layout),
            or an exact layout name such as "Layout1".
        monochrome: render everything black on white (print-friendly).
        paper: canonical page name from :data:`PAPER_CHOICES`; "auto" keeps the
            paperspace page size defined in the drawing and auto-detects a page
            for modelspace content (capped at ISO A0).
        scale: extra scale factor applied to the drawing content.
        margin: page margin in mm; None keeps the default (0 for auto paper,
            5 mm for a named paper size).
        fit: True/False forces fit-to-page on or off; None (default) uses the
            sensible per-layout behaviour (fit for modelspace, exact CAD plot
            scale for paperspace).
        jobs: worker processes for batch conversion; 0 = one per CPU core.
        output_dir: None writes each PDF next to its source file.
        skip_existing: skip a file when its target PDF already exists.
        font_dirs: extra directories scanned for SHX/SHP/LFF/TTF fonts.
        converter_timeout: seconds allowed for one external DWG->DXF run.
        large_dxf_mb: intermediate DXF size above which a "large drawing,
            rendering can take minutes" warning is emitted. Measured on a
            2020-class desktop: a 57 MB DXF (~14k modelspace entities, 5.7k
            block inserts) takes ~3 min for modelspace; a paperspace layout
            with several viewports re-renders the model per viewport and can
            take far longer (~23 min observed for 3 viewports).
        chunk_threshold: layouts with at least this many entities use the
            chunked render path (see renderer._render_layout_chunked). PyMuPDF's
            backend commits the whole drawing through one shape, which is
            superlinear: an 85k-record drawing ran >15 min unfinished while the
            same geometry in 8k-record chunks finished in 123 s.
        chunk_records: recorded commands per chunked-commit page.
        threads: worker processes for chunked rendering of ONE big layout;
            0 = auto (all cores when converting a single file, 1 when the
            batch itself already runs files in parallel). Nested pools are
            never created.
        quality: render quality preset — "fast" (效率版), "balanced" (default),
            "high" (质量版). Controls curve segment counts and flattening
            tolerance; see QUALITY_PRESETS.
        verbose: surface extra diagnostics in logs.
    """

    layout: str = "auto"
    monochrome: bool = False
    paper: str = "auto"
    scale: float = 1.0
    margin: float | None = None
    fit: bool | None = None
    jobs: int = 0
    output_dir: str | None = None
    skip_existing: bool = False
    font_dirs: tuple[str, ...] = field(default=())
    converter_timeout: int = 600
    large_dxf_mb: float = 5.0
    chunk_threshold: int = 20000
    chunk_records: int = 8000
    threads: int = 0
    quality: str = "balanced"
    verbose: bool = False

    def validate(self) -> ConvertOptions:
        """Return a copy with normalized values; raises OptionError if invalid."""
        layout = self.layout.strip()
        if layout.lower() in LAYOUT_CHOICES:
            layout = layout.lower()
        elif not layout:
            raise OptionError("layout must not be empty")
        paper = resolve_paper(self.paper)
        if self.scale <= 0:
            raise OptionError(f"scale must be > 0, got {self.scale}")
        if self.margin is not None and self.margin < 0:
            raise OptionError(f"margin must be >= 0, got {self.margin}")
        if self.jobs < 0:
            raise OptionError(f"jobs must be >= 0, got {self.jobs}")
        if self.converter_timeout <= 0:
            raise OptionError(f"converter-timeout must be > 0, got {self.converter_timeout}")
        if self.large_dxf_mb <= 0:
            raise OptionError(f"large-dxf-mb must be > 0, got {self.large_dxf_mb}")
        if self.chunk_threshold < 0:
            raise OptionError(f"chunk-threshold must be >= 0, got {self.chunk_threshold}")
        if self.chunk_records <= 0:
            raise OptionError(f"chunk-records must be > 0, got {self.chunk_records}")
        if self.threads < 0:
            raise OptionError(f"threads must be >= 0, got {self.threads}")
        quality = self.quality.strip().lower()
        if quality not in QUALITY_CHOICES:
            raise OptionError(
                f"unknown quality {self.quality!r} (choose from: {', '.join(QUALITY_CHOICES)})"
            )
        return ConvertOptions(
            layout=layout,
            monochrome=bool(self.monochrome),
            paper=paper,
            scale=float(self.scale),
            margin=self.margin,
            fit=self.fit,
            jobs=int(self.jobs),
            output_dir=self.output_dir,
            skip_existing=bool(self.skip_existing),
            font_dirs=tuple(str(p) for p in self.font_dirs),
            converter_timeout=int(self.converter_timeout),
            large_dxf_mb=float(self.large_dxf_mb),
            chunk_threshold=int(self.chunk_threshold),
            chunk_records=int(self.chunk_records),
            threads=int(self.threads),
            quality=quality,
            verbose=bool(self.verbose),
        )
