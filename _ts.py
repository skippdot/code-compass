"""Thin adapter over tree-sitter-language-pack's binding.

That binding exposes everything as zero-arg methods (`node.kind()`,
`node.start_byte()`, `node.start_position().row`) and makes `root_node` a
method too. Rather than sprinkle those quirks through the chunker, we wrap a
node in `TSNode` and expose plain properties. If we ever swap the binding,
only this file changes.
"""

from dataclasses import dataclass
from functools import cache

from tree_sitter_language_pack import get_parser

# path suffix -> tree-sitter language name
EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}


def detect_language(path: str) -> str | None:
    for ext, lang in EXT_TO_LANG.items():
        if path.endswith(ext):
            return lang
    return None


@dataclass
class TSNode:
    """Property-style view over one binding node."""

    _n: object
    _src: bytes  # the utf-8 source, for slicing text by byte range

    @property
    def kind(self) -> str:
        return self._n.kind()

    @property
    def start_line(self) -> int:
        return self._n.start_position().row

    @property
    def end_line(self) -> int:
        return self._n.end_position().row

    @property
    def text(self) -> str:
        return self._src[self._n.start_byte():self._n.end_byte()].decode(
            "utf-8", errors="replace"
        )

    @property
    def children(self) -> list["TSNode"]:
        n = self._n
        return [TSNode(n.child(i), self._src) for i in range(n.child_count())]

    def text_through(self, other: "TSNode") -> str:
        """Contiguous source from this node's start through `other`'s end —
        used to fuse a run of adjacent members into one grouped chunk."""
        return self._src[self._n.start_byte():other._n.end_byte()].decode(
            "utf-8", errors="replace"
        )

    @property
    def name(self) -> str | None:
        """Identifier of a definition, via the 'name' field if present."""
        field = self._n.child_by_field_name("name")
        if field is None:
            return None
        return self._src[field.start_byte():field.end_byte()].decode(
            "utf-8", errors="replace"
        )


@cache
def _parser(lang: str):
    return get_parser(lang)


def parse(lang: str, source: str) -> TSNode:
    """Parse `source` and return its root as a TSNode."""
    src_bytes = source.encode("utf-8")
    root = _parser(lang).parse(source).root_node()
    return TSNode(root, src_bytes)
