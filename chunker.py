"""Syntax-aware chunking.

A chunk is a self-contained, semantically meaningful slice of code — a function,
method, or class — rather than an arbitrary N-line window. Coherent chunks embed
into sharper vectors, which is the single biggest quality lever in the retriever.

Strategy:
  * Parse the file, walk the tree, emit each *definition* as its own chunk.
  * A definition too large to embed whole (e.g. a 140-line model class) is split
    smartly: substantial methods stay their own named chunks, while the class's
    fields and its run of tiny one-line properties are *grouped* into contextful
    chunks (carrying the enclosing name as `parent`). This both rescues the
    class-body fields — which a naive method-only descent silently drops — and
    keeps 2-line properties from becoming context-starved chunks that never rank.
  * Files in an unsupported language, or that produce no definitions at all,
    fall back to fixed line windows so nothing is silently dropped.
"""

from dataclasses import dataclass

from _ts import TSNode, detect_language, parse

# Bump when chunking logic changes so the indexer's content hash invalidates and
# re-chunks even when file contents are unchanged.
CHUNKER_VERSION = 2

# Node kinds (matched as suffix) that deserve to be their own chunk, across the
# languages we support. tree-sitter names differ per grammar, hence the spread.
DEFINITION_KINDS = (
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition",
    "method_declaration",
    "class_definition",
    "class_declaration",
    "impl_item",
    "interface_declaration",
    "struct_item",
)

MAX_CHUNK_LINES = 120     # a definition larger than this is split
MIN_STANDALONE_LINES = 5  # smaller members are grouped, not emitted alone
WINDOW_LINES = 60         # fallback window size
WINDOW_OVERLAP = 10       # overlap so a boundary symbol isn't orphaned


@dataclass
class Chunk:
    path: str
    language: str
    kind: str         # tree-sitter node kind, or "window" for the fallback
    name: str | None  # symbol name when known
    start_line: int   # 1-based, inclusive
    end_line: int     # 1-based, inclusive
    text: str         # chunk source, possibly prefixed with a parent breadcrumb
    parent: str | None = None


def _is_definition(node: TSNode) -> bool:
    return node.kind.endswith(DEFINITION_KINDS)


def _make_chunk(
    node: TSNode, path: str, language: str, parent: str | None
) -> Chunk:
    text = node.text
    if parent:  # breadcrumb gives the embedder the enclosing scope as context
        text = f"# in {parent}:\n{text}"
    return Chunk(
        path=path,
        language=language,
        kind=node.kind,
        name=node.name,
        start_line=node.start_line + 1,
        end_line=node.end_line + 1,
        text=text,
        parent=parent,
    )


def _make_group_chunk(
    first: TSNode, last: TSNode, path: str, language: str, parent: str | None
) -> Chunk:
    """One chunk fusing a contiguous run of members (fields, tiny properties)."""
    text = first.text_through(last)
    if parent:
        text = f"# in {parent}:\n{text}"
    return Chunk(
        path=path,
        language=language,
        kind="members",
        name=None,
        start_line=first.start_line + 1,
        end_line=last.end_line + 1,
        text=text,
        parent=parent,
    )


def _members(node: TSNode) -> list[TSNode]:
    """The members of a class/function: children of its body block if it has
    one (so we walk fields + methods), else the node's own children."""
    for child in node.children:
        if child.kind.endswith(("block", "body", "declaration_list")):
            return child.children
    return node.children


def _effective(node: TSNode) -> TSNode:
    """Unwrap a `decorated_definition` (@property/@staticmethod) to the inner
    def, so a decorated method is recognised — and sized — as a definition."""
    if node.kind == "decorated_definition":
        for child in node.children:
            if _is_definition(child):
                return child
    return node


def _split_large(
    node: TSNode, path: str, language: str, parent: str | None, out: list[Chunk]
) -> None:
    """Split a too-large definition. Substantial members become their own named
    chunks (recursing if a member is itself too large); fields and runs of tiny
    members are grouped into contextful chunks so nothing is dropped and no
    2-line property ends up a context-starved chunk."""
    inner_parent = node.name or parent
    before = len(out)
    buf: list[TSNode] = []

    def flush() -> None:
        if buf:
            out.append(_make_group_chunk(buf[0], buf[-1], path, language, inner_parent))
            buf.clear()

    for child in _members(node):
        eff = _effective(child)
        span = eff.end_line - eff.start_line + 1
        if _is_definition(eff) and span > MAX_CHUNK_LINES:
            flush()
            _split_large(eff, path, language, inner_parent, out)
        elif _is_definition(eff) and span >= MIN_STANDALONE_LINES:
            flush()
            out.append(_make_chunk(eff, path, language, inner_parent))
        else:  # field, docstring, or tiny member -> group with neighbours
            buf.append(child)
            if buf[-1].end_line - buf[0].start_line + 1 >= MAX_CHUNK_LINES:
                flush()
    flush()
    if len(out) == before:  # empty body — don't drop the node
        out.append(_make_chunk(node, path, language, parent))


def _collect(
    node: TSNode,
    path: str,
    language: str,
    parent: str | None,
    out: list[Chunk],
) -> None:
    """Recursively walk `node`'s subtree, appending Chunks to `out`.

    Three cases per node:
      * Definition that fits in MAX_CHUNK_LINES -> emit it whole, stop.
      * Definition too large -> `_split_large` (named methods kept, fields and
        tiny members grouped), carrying this node's name as `parent`.
      * Anything else (module, block, statement) -> descend to find definitions.
    """
    span = node.end_line - node.start_line + 1
    if _is_definition(node) and span <= MAX_CHUNK_LINES:
        out.append(_make_chunk(node, path, language, parent))
        return
    if _is_definition(node):
        _split_large(node, path, language, parent, out)
        return
    for child in node.children:
        _collect(child, path, language, parent, out)


def _window_fallback(
    source: str, path: str, language: str | None
) -> list[Chunk]:
    lines = source.splitlines()
    out: list[Chunk] = []
    step = WINDOW_LINES - WINDOW_OVERLAP
    for start in range(0, len(lines), step):
        window = lines[start:start + WINDOW_LINES]
        if not window:
            break
        out.append(
            Chunk(
                path=path,
                language=language or "text",
                kind="window",
                name=None,
                start_line=start + 1,
                end_line=start + len(window),
                text="\n".join(window),
            )
        )
        if start + WINDOW_LINES >= len(lines):
            break
    return out


def chunk_file(path: str, source: str, language: str | None = None) -> list[Chunk]:
    language = language or detect_language(path)
    if language is None:
        return _window_fallback(source, path, language)

    root = parse(language, source)
    chunks: list[Chunk] = []
    _collect(root, path, language, None, chunks)

    # tree-sitter parsed it but found no definitions (e.g. a script of bare
    # statements) — don't drop the file, window it.
    if not chunks:
        return _window_fallback(source, path, language)
    return chunks
