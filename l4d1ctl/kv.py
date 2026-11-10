"""Minimal Valve KeyValues ("VDF") text parser and writer.

This is dependency-free on purpose: the port tooling has to run on a phone
(Termux), on a desktop, and inside CI without pip access.

Supported syntax:

    "key"        "value"
    "key"        { "sub" "value" }
    key          value            // bare tokens are accepted, like Valve's parser
    // comment
    "key" "value" [$WIN32]        // platform conditional, recorded but not applied
    #base "file.txt"              // kept as a node, never expanded implicitly

The parser is forgiving: stray closing braces are ignored and an unterminated
file is reported with a ``KVParseError`` instead of silently truncating.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence


class KVParseError(ValueError):
    """Raised when a KeyValues document cannot be parsed."""


@dataclass
class KVNode:
    """One key in a KeyValues document."""

    key: str
    value: Optional[str] = None
    children: List["KVNode"] = field(default_factory=list)
    platform: Optional[str] = None

    # -- structure ---------------------------------------------------------
    @property
    def is_section(self) -> bool:
        return bool(self.children)

    def __iter__(self) -> Iterator["KVNode"]:
        return iter(self.children)

    def __len__(self) -> int:
        return len(self.children)

    def __bool__(self) -> bool:
        # A node is a value object: leaf nodes must still be truthy, otherwise
        # `if node and node.value` style checks silently skip real values.
        return True

    def __getitem__(self, key: str) -> "KVNode":
        found = self.find(key)
        if found is None:
            raise KeyError(key)
        return found

    def __contains__(self, key: str) -> bool:
        return self.find(key) is not None

    # -- lookup ------------------------------------------------------------
    def find(self, key: str) -> Optional["KVNode"]:
        """First child with ``key`` (case-insensitive, Valve style)."""
        lowered = key.lower()
        for child in self.children:
            if child.key.lower() == lowered:
                return child
        return None

    def find_all(self, key: str) -> List["KVNode"]:
        lowered = key.lower()
        return [c for c in self.children if c.key.lower() == lowered]

    def get(self, key: str, default: str = "") -> str:
        node = self.find(key)
        if node is None or node.value is None:
            return default
        return node.value

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self.get(key, "")
        try:
            return int(raw.strip(), 0)
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key, "").strip().lower()
        if not raw:
            return default
        return raw not in ("0", "false", "no", "off")

    def section(self, key: str) -> "KVNode":
        """Return child section ``key``, creating it when absent."""
        node = self.find(key)
        if node is None:
            node = KVNode(key)
            self.children.append(node)
        return node

    def set_value(self, key: str, value: object) -> "KVNode":
        node = self.find(key)
        if node is None:
            node = KVNode(key)
            self.children.append(node)
        node.value = str(value)
        node.children = []
        return node

    def remove(self, key: str) -> bool:
        lowered = key.lower()
        for index, child in enumerate(self.children):
            if child.key.lower() == lowered:
                del self.children[index]
                return True
        return False

    # -- conversion --------------------------------------------------------
    def to_dict(self) -> dict:
        if self.children:
            return {c.key: c.to_dict() for c in self.children}
        return {self.key: self.value or ""}

    def walk(self) -> Iterator["KVNode"]:
        yield self
        for child in self.children:
            yield from child.walk()


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>[ \t\r\n\f\v]+)
    | (?P<comment>//[^\n]*)
    | (?P<str>"(?:[^"\\]|\\.)*")
    | (?P<open>\{)
    | (?P<close>\})
    | (?P<bare>[^\s"{}]+)
    """,
    re.VERBOSE,
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}


def _unescape(raw: str) -> str:
    out = []
    it = iter(raw)
    for ch in it:
        if ch != "\\":
            out.append(ch)
            continue
        try:
            nxt = next(it)
        except StopIteration:
            out.append("\\")
            break
        out.append(_ESCAPES.get(nxt, nxt))
    return "".join(out)


def _escape(raw: str) -> str:
    return raw.replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class _Token:
    kind: str  # atom | open | close
    value: str = ""


def _tokenize(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    pos = 0
    length = len(text)
    while pos < length:
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise KVParseError(f"unexpected character {text[pos]!r} at offset {pos}")
        pos = match.end()
        kind = match.lastgroup or ""
        if kind in ("ws", "comment"):
            continue
        if kind == "str":
            tokens.append(_Token("atom", _unescape(match.group()[1:-1])))
        elif kind == "open":
            tokens.append(_Token("open"))
        elif kind == "close":
            tokens.append(_Token("close"))
        else:
            tokens.append(_Token("atom", match.group()))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_PLATFORM_RE = re.compile(r"^\[[^\]]*\]$")


def _is_platform_token(token: _Token) -> bool:
    return token.kind == "atom" and bool(_PLATFORM_RE.match(token.value))


def _parse_block(tokens: Sequence[_Token], pos: int, top: bool) -> tuple[List[KVNode], int]:
    nodes: List[KVNode] = []
    while pos < len(tokens):
        token = tokens[pos]
        if token.kind == "close":
            if top:
                pos += 1
                continue
            return nodes, pos + 1
        if token.kind == "open":
            # Stray '{': skip it rather than blowing up on a malformed file.
            pos += 1
            continue

        node = KVNode(token.value)
        pos += 1
        if pos < len(tokens) and tokens[pos].kind == "open":
            node.children, pos = _parse_block(tokens, pos + 1, top=False)
        elif pos < len(tokens) and tokens[pos].kind == "atom":
            node.value = tokens[pos].value
            pos += 1
            if pos < len(tokens) and _is_platform_token(tokens[pos]):
                node.platform = tokens[pos].value.strip("[]$")
                pos += 1
        nodes.append(node)

    if not top:
        raise KVParseError("unexpected end of file: unbalanced '{'")
    return nodes, pos


def loads(text: str) -> List[KVNode]:
    """Parse ``text`` into a list of top-level :class:`KVNode`."""
    if text.startswith("\ufeff"):
        text = text[1:]
    nodes, _ = _parse_block(_tokenize(text), 0, top=True)
    return nodes


def load_file(path: Path, encoding: str = "utf-8", errors: str = "replace") -> List[KVNode]:
    data = Path(path).read_bytes()
    return loads(data.decode(encoding, errors))


def find(nodes: Sequence[KVNode], key: str) -> Optional[KVNode]:
    lowered = key.lower()
    for node in nodes:
        if node.key.lower() == lowered:
            return node
    return None


def flatten(nodes: Iterable[KVNode], key: str) -> List[KVNode]:
    """Collect every node named ``key`` at any depth."""
    lowered = key.lower()
    out: List[KVNode] = []
    for node in nodes:
        for walked in node.walk():
            if walked.key.lower() == lowered:
                out.append(walked)
    return out


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def dumps(nodes: Sequence[KVNode], indent: str = "\t", header: Optional[str] = None) -> str:
    """Serialize nodes back to Valve-style text."""
    lines: List[str] = []
    if header:
        lines.append(f"// {header}\n")
    _emit(nodes, 0, indent, lines)
    return "".join(lines)


def _emit(nodes: Sequence[KVNode], level: int, indent: str, out: List[str]) -> None:
    pad = indent * level
    for node in nodes:
        if node.children:
            out.append(f'{pad}"{_escape(node.key)}"\n{pad}{{\n')
            _emit(node.children, level + 1, indent, out)
            out.append(f"{pad}}}\n")
        else:
            out.append(f'{pad}"{_escape(node.key)}"\t\t"{_escape(node.value or "")}"\n')


def write_file(path: Path, nodes: Sequence[KVNode], header: Optional[str] = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(nodes, header=header), encoding="utf-8")
