"""What the UI's fonts are, and whether this engine can load them.

The engine's font pipeline is FreeType-only: a scheme font either resolves to a
TrueType/OpenType face the manager can build, or it resolves to nothing and every
label loses its text. L4D1's content ships Valve's own ``.vfont`` bitmap fonts
(``HALFLIFE2.vfont``, ``hl2crosshairs.vfont``, ``HL2EP2.vfont`` …), which FreeType
rejects with ``FT_Err_Unknown_File_Format`` no matter how the bytes arrive.

Two different fixes follow from that, and picking the wrong one wastes a build:

* if the scheme only names ``.vfont`` custom fonts, text cannot appear without
  either a ``.vfont`` reader or a scheme that names a TTF instead;
* if it names system fonts (``"name" "Verdana"``), the failure is the *read* of the
  file fontconfig substitutes, which is a much smaller problem.

This module answers which of those is true by reading the scheme, so the answer
comes from the content rather than from a hunch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import kv

# Files a Source UI scheme can live in, most specific first.
SCHEME_FILES = ("resource/SourceScheme.res", "resource/ClientScheme.res",
                "resource/ui/scheme.res", "resource/scheme.res")

# Extensions a scheme's CustomFontFiles block can name.
FONT_EXTENSIONS = (".vfont", ".ttf", ".otf", ".ttc", ".fnt")

# Keys that live *inside* a font block. Seeing one directly under Fonts means the
# scheme's braces are unbalanced there, not that the font is called "name".
FONT_ATTRIBUTES = frozenset({
    "name", "custom", "tall", "weight", "blur", "scanlines", "antialias",
    "additive", "outline", "rotary", "shadow", "border", "symbol", "bitmap",
    "yres", "dropshadow", "resolution", "flags",
})


def _looks_like_font_path(text: str) -> bool:
    """A path, not a numbered index. Valve writes CustomFontFiles as "1" "a.vfont"."""
    candidate = text.strip().strip('"')
    if not candidate:
        return False
    if "/" in candidate or "\\" in candidate:
        return True
    return candidate.lower().endswith(FONT_EXTENSIONS)


@dataclass
class SchemeFont:
    """One entry of the scheme's ``Fonts`` block."""

    key: str                 # the block's own name, e.g. "Default", "DefaultBold"
    name: str = ""           # "name" value: a system font family
    custom: str = ""         # "custom" value: a custom font file
    tall: str = ""
    weight: str = ""
    flags: Dict[str, str] = field(default_factory=dict)

    @property
    def source(self) -> str:
        if self.custom:
            return self.custom
        if self.via_custom_file:
            return f"{self.name} ({self.via_custom_file})"
        return self.name or "(nothing)"

    # Filled in by read(): a scheme can point at a custom font either through
    # "custom" or by naming it, and L4D1's scheme does the latter - the font is
    # called HALFLIFE2, and resource/HALFLIFE2.vfont is the file behind it.
    via_custom_file: str = ""

    @property
    def is_vfont(self) -> bool:
        source = self.custom or self.via_custom_file
        return source.lower().endswith(".vfont")


@dataclass
class FontReport:
    scheme: Optional[Path] = None
    parsed: bool = False
    error: str = ""
    custom_font_files: List[str] = field(default_factory=list)
    unparsed_nodes: List[str] = field(default_factory=list)
    fonts: List[SchemeFont] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)

    @property
    def vfont_only(self) -> bool:
        """True when nothing in the scheme can ever produce a face in this engine."""
        if not self.fonts:
            return False
        return all(f.is_vfont for f in self.fonts)

    def summary(self) -> str:
        if not self.parsed:
            return self.error or "no scheme found"
        vfonts = [f for f in self.fonts if f.is_vfont]
        ttfs = [f for f in self.fonts if f.custom and not f.is_vfont]
        system = [f for f in self.fonts
                  if not f.custom and not f.via_custom_file]
        parts = [f"{len(self.fonts)} font(s)"]
        if vfonts:
            parts.append(f"{len(vfonts)} on .vfont (cannot load here)")
        if ttfs:
            parts.append(f"{len(ttfs)} on a custom TTF/OTF")
        if system:
            parts.append(f"{len(system)} on a system font name")
        return ", ".join(parts)


def find_scheme(root: Path) -> Optional[Path]:
    """The scheme the engine will actually read, searching the usual spellings."""
    root = Path(root).expanduser()
    for relative in SCHEME_FILES:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    # A game directory passed as the root, or one nested under it.
    for candidate in sorted(root.glob("*/resource/*[Ss]cheme*.res")):
        if candidate.is_file():
            return candidate
    return None


def _find_block(nodes: Sequence[kv.KVNode], *names: str) -> Optional[kv.KVNode]:
    """Depth-first search for the first block called one of ``names``."""
    wanted = {name.lower() for name in names}
    for node in nodes:
        if node.key.lower() in wanted and node.children:
            return node
        found = _find_block(node.children, *names)
        if found is not None:
            return found
    return None


def _fonts_block(nodes: Sequence[kv.KVNode]) -> Optional[kv.KVNode]:
    return _find_block(nodes, "fonts")


def _custom_font_files_block(nodes: Sequence[kv.KVNode]) -> Optional[kv.KVNode]:
    return _find_block(nodes, "customfontfiles")


def _custom_font_files(nodes: Sequence[kv.KVNode]) -> List[str]:
    """Paths from the ``CustomFontFiles`` block.

    Valve writes bare tokens here - ``"resource/HALFLIFE2.vfont"`` - which the
    KeyValues reader shows as a key with no value, so the path can arrive in either
    field. Both count.
    """
    block = _custom_font_files_block(nodes)
    if block is None:
        return []
    # Two shapes are in the wild, and L4D1 uses both across its files: bare tokens
    # ("a.vfont" "b.vfont" parses as key/value) and numbered indices
    # ("1" "resource/HALFLIFE2.vfont"). Take whichever side of the pair is a path,
    # and never the index.
    names: List[str] = []
    for child in block.children:
        for candidate in (child.key.strip(), (child.value or "").strip()):
            if _looks_like_font_path(candidate) and candidate not in names:
                names.append(candidate)
    return names


def _font_values(node: kv.KVNode) -> kv.KVNode:
    """The block that holds a font's settings.

    A scheme font looks like ``Default { "1" { "name" … } }`` - one child per
    resolution - so the values are one level down. Some schemes skip that level.
    """
    if node.children and not any(key in node for key in ("name", "custom")):
        for child in node.children:
            if any(key in child for key in ("name", "custom")):
                return child
        if node.children:
            return node.children[0]
    return node


def read(root: Path) -> FontReport:
    """Parse the scheme and report every font the UI can ask for."""
    root = Path(root).expanduser()
    report = FontReport()
    scheme = find_scheme(root)
    if scheme is None:
        report.error = (f"no UI scheme found under {root} "
                        f"(looked for {', '.join(SCHEME_FILES)})")
        return report

    report.scheme = scheme
    try:
        nodes = kv.load_file(scheme)
    except (kv.KVParseError, OSError) as exc:
        report.error = f"{scheme}: {exc}"
        return report

    report.parsed = True
    report.custom_font_files = [name for name in _custom_font_files(nodes) if name]

    block = _fonts_block(nodes)
    if block is None:
        report.error = f"{scheme} has no Fonts block"
        return report

    for child in block.children:
        if child.key.isdigit() or child.key.strip().lower() in FONT_ATTRIBUTES:
            # Unbalanced braces in the scheme put a font's insides here directly.
            report.unparsed_nodes.append(child.key)
            continue
        values = _font_values(child)
        font = SchemeFont(key=child.key)
        font.name = values.get("name") if "name" in values else ""
        custom = values.find("custom")
        if custom is not None and custom.value:
            font.custom = custom.value
        font.tall = values.get("tall") if "tall" in values else ""
        weight = values.find("weight")
        if weight is not None and weight.value:
            font.weight = weight.value
        for flag in ("blur", "scanlines", "antialias", "additive", "outline",
                     "rotary", "shadow", "border"):
            node = values.find(flag)
            if node is not None and node.value is not None:
                font.flags[flag] = node.value
        report.fonts.append(font)

    # A font whose "name" matches a registered custom font file is that file; the
    # scheme is not asking fontconfig for it.
    by_stem = {}
    for path in report.custom_font_files:
        by_stem[Path(path).stem.lower()] = path
    for report_font in report.fonts:
        if not report_font.custom and report_font.name:
            matched = by_stem.get(report_font.name.lower())
            if matched:
                report_font.via_custom_file = matched

    game = scheme.parent.parent
    for name in report.custom_font_files:
        if not (game / name).is_file():
            report.missing_files.append(name)
    return report


def render(report: FontReport, *, limit: int = 40) -> List[str]:
    lines: List[str] = []
    if not report.parsed:
        lines.append(report.error or "no scheme found")
        return lines

    lines.append(f"scheme: {report.scheme}")
    lines.append(f"        {report.summary()}")
    if report.custom_font_files:
        lines.append("")
        lines.append("CustomFontFiles:")
        for name in report.custom_font_files:
            mark = "  (missing)" if name in report.missing_files else ""
            lines.append(f"  {name}{mark}")

    if report.fonts:
        lines.append("")
        lines.append("Fonts:")
        width = max(len(f.key) for f in report.fonts)
        for font in report.fonts[:limit]:
            bits = [f"tall {font.tall or '?'}"]
            if font.weight:
                bits.append(f"weight {font.weight}")
            lines.append(f"  {font.key.ljust(width)}  {font.source}  ({', '.join(bits)})")
        if len(report.fonts) > limit:
            lines.append(f"  ... and {len(report.fonts) - limit} more")

    if report.unparsed_nodes:
        lines.append("")
        lines.append(f"{len(report.unparsed_nodes)} node(s) under Fonts were not font")
        lines.append("entries (" + ", ".join(sorted(set(report.unparsed_nodes))[:6]) +
                     "): the scheme's braces are unbalanced there. Harmless for this")
        lines.append("report, but it means the block it belongs to is not being read.")

    lines.append("")
    if report.vfont_only:
        lines.append("Every font in this scheme is a .vfont, and this engine's font")
        lines.append("pipeline is FreeType-only: .vfont is Valve's bitmap format, so")
        lines.append("FreeType answers ERROR 2 and no label gets text. That is not a")
        lines.append("bug in the read path - the fix has to be a .vfont reader or a")
        lines.append("scheme that names a TTF (see docs/L4D1/FONTS.md).")
    elif report.fonts and all(f.custom and not f.is_vfont for f in report.fonts):
        lines.append("Every font here is a custom TTF/OTF: if the UI still has no text,")
        lines.append("the failure is the read of those files, not the format.")
    else:
        lines.append("Mixed sources: some fonts are system names (fontconfig resolves")
        lines.append("those), some are custom files. A missing label is then a per-font")
        lines.append("question - read the lines above.")
    return lines


__all__ = ["FontReport", "SCHEME_FILES", "SchemeFont", "find_scheme", "read", "render"]
