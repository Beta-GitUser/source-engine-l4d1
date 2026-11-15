"""Dissect a GameUI ``.res`` file: what it asks for, and whether this engine has it.

A menu resource is a panel layout: nested KeyValues where each block names a control
class, a label and the command behind it. Two things go wrong between an old game and
a newer GameUI, and they look identical on screen:

* the panel exists but has no text (fonts), or
* the layout asks for a control class this build does not implement, so the entry is
  silently skipped and the menu is short by however many items.

This module reads the file and answers the second question by scanning a source tree
for the classes it actually declares, so "the menu is missing Settings" can be traced
to a name instead of a hunch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from . import kv

# Keys that carry the meaning of a menu entry. Valve's layouts use the first group;
# a differently-shaped menu format uses words like these too, which is why they are
# what the dissector keys off.
CLASS_KEYS = ("ControlName", "ControlType", "type", "class")
LABEL_KEYS = ("LabelText", "ButtonText", "label", "text", "texture")
ACTION_KEYS = ("Command", "command", "cvar", "action", "tooltip")

DECLARE_RE = re.compile(r"DECLARE_CLASS_SIMPLE\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,")
CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*public", re.MULTILINE)


@dataclass
class MenuEntry:
    path: str                 # nested key path, e.g. "MainMenu/PlayButton"
    control: str = ""         # control class the layout asks for
    label: str = ""
    action: str = ""
    line: int = 0


@dataclass
class MenuReport:
    res: Optional[Path] = None
    parsed: bool = False
    error: str = ""
    entries: List[MenuEntry] = field(default_factory=list)
    top_level: List[str] = field(default_factory=list)
    unknown_controls: Dict[str, int] = field(default_factory=dict)
    known_classes: int = 0
    scanned_source: Optional[Path] = None

    @property
    def controls(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.entries:
            if entry.control:
                counts[entry.control] = counts.get(entry.control, 0) + 1
        return counts


def scan_classes(source: Path) -> Dict[str, Path]:
    """Panel classes declared in a source tree, by name.

    Both spellings matter: ``DECLARE_CLASS_SIMPLE( CFoo, CBar )`` registers the name
    vgui looks up, and a plain ``class CFoo : public vgui::Panel`` without the macro
    still compiles into something a layout cannot instantiate.
    """
    source = Path(source).expanduser()
    found: Dict[str, Path] = {}
    if not source.is_dir():
        return found
    for path in source.rglob("*.cpp"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for pattern in (DECLARE_RE, CLASS_RE):
            for match in pattern.finditer(text):
                found.setdefault(match.group(1), path)
    return found


def _pick(node: kv.KVNode, keys: Sequence[str]) -> str:
    for key in keys:
        found = node.find(key)
        if found is not None and found.value:
            return found.value.strip()
    return ""


def _walk(node: kv.KVNode, path: str, out: List[MenuEntry]) -> None:
    control = _pick(node, CLASS_KEYS)
    label = _pick(node, LABEL_KEYS)
    action = _pick(node, ACTION_KEYS)
    if control or action:
        out.append(MenuEntry(path=path, control=control, label=label, action=action,
                             line=node.line))
    for child in node.children:
        _walk(child, f"{path}/{child.key}" if path else child.key, out)


def read(res: Path, source: Optional[Path] = None) -> MenuReport:
    res = Path(res).expanduser()
    report = MenuReport(res=res)
    if not res.is_file():
        report.error = f"no such file: {res}"
        return report
    try:
        nodes = kv.load_file(res)
    except (kv.KVParseError, OSError) as exc:
        report.error = f"{res}: {exc}"
        return report

    report.parsed = True
    report.top_level = [node.key for node in nodes]
    for node in nodes:
        _walk(node, node.key, report.entries)

    if source is not None:
        classes = scan_classes(source)
        report.scanned_source = Path(source)
        report.known_classes = len(classes)
        for name, count in report.controls.items():
            if name not in classes:
                report.unknown_controls[name] = count
    return report


def render(report: MenuReport, *, limit: int = 60, trace: bool = False) -> List[str]:
    lines: List[str] = []
    if not report.parsed:
        lines.append(report.error or "not parsed")
        return lines

    lines.append(f"resource: {report.res}")
    lines.append(f"          top level: {', '.join(report.top_level) or '(none)'}")

    if not report.entries:
        lines.append("")
        lines.append("Nothing in this file names a control class or a command, so it is")
        lines.append("not a panel layout in the shape this reader understands. Most likely")
        lines.append("its keys are a plain list; --trace prints them as they were parsed.")
        return lines

    lines.append("")
    lines.append(f"{len(report.entries)} entry/entries")
    width = max((len(e.control) for e in report.entries), default=1)
    for entry in report.entries[:limit]:
        bits = []
        if entry.label:
            bits.append(f"'{entry.label}'")
        if entry.action:
            bits.append(f"-> {entry.action}")
        if trace:
            bits.append(f"[line {entry.line}]")
        lines.append(f"  {entry.path}")
        lines.append(f"    {entry.control.ljust(width)}  {' '.join(bits)}")
    if len(report.entries) > limit:
        lines.append(f"  ... and {len(report.entries) - limit} more")

    if report.scanned_source is not None:
        lines.append("")
        if report.known_classes == 0:
            lines.append(f"no panel classes found under {report.scanned_source}")
        elif report.unknown_controls:
            lines.append("Classes this layout asks for that the scanned tree does not")
            lines.append("declare (the entry is skipped, the menu comes up short):")
            for name, count in sorted(report.unknown_controls.items()):
                lines.append(f"  {name}  ({count}x)")
        else:
            lines.append(f"Every class this layout names exists in "
                         f"{report.scanned_source} ({report.known_classes} known).")
    return lines


__all__ = ["MenuEntry", "MenuReport", "read", "render", "scan_classes"]
