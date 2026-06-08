"""Smoke tests for chunker.py — all offline (no API)."""

from chunker import MAX_CHUNK_LINES, chunk_file


def _names(chunks):
    return [(c.kind, c.name, c.parent) for c in chunks]


def test_top_level_function_and_small_class():
    src = (
        "import os\n\n"
        "def add(a, b):\n    return a + b\n\n"
        "class Foo:\n"
        "    def bar(self):\n        return 1\n"
        "    def baz(self):\n        return 2\n"
    )
    chunks = chunk_file("m.py", src)
    kinds = _names(chunks)
    # add() is its own chunk; small class Foo is emitted WHOLE (one chunk)
    assert ("function_definition", "add", None) in kinds
    assert ("class_definition", "Foo", None) in kinds
    foo = next(c for c in chunks if c.name == "Foo")
    assert "def bar" in foo.text and "def baz" in foo.text  # methods inside


def test_large_class_groups_fields_keeps_methods():
    # a class over MAX_CHUNK_LINES: fields + many tiny @property + one real method
    fields = "\n".join(f"    f{i} = Field()" for i in range(60))
    props = "\n".join(
        f"    @property\n    def p{i}(self):\n        return self.f{i}" for i in range(30)
    )
    body = "\n".join(f"        s += {i}" for i in range(10))
    method = f"    def compute(self):\n        s = 0\n{body}\n        return s\n"
    chunks = chunk_file("big.py", f"class Big:\n{fields}\n{props}\n{method}")

    # the substantial method stays its own named chunk...
    assert any(c.name == "compute" and c.parent == "Big" for c in chunks)
    # ...while fields + tiny properties are grouped (not 60 lonely chunks)
    grouped = [c for c in chunks if c.kind == "members"]
    assert grouped, "fields/tiny members should be grouped"
    # the class fields are recovered, not silently dropped
    assert any("f0 = Field()" in c.text for c in chunks)
    # tiny properties travel together with context, not one-per-chunk
    assert any("def p0" in c.text and "def p1" in c.text for c in grouped)


def test_oversized_leaf_function_is_not_dropped():
    huge = "\n".join(f"    y{i} = {i}" for i in range(MAX_CHUNK_LINES + 10))
    chunks = chunk_file("leaf.py", f"def lonely():\n{huge}\n    return y0\n")
    assert chunks, "a huge function with no inner defs must still produce chunks"
    assert any("y0 = 0" in c.text for c in chunks)


def test_script_without_definitions_falls_back_to_windows():
    src = "\n".join(f"print({i})" for i in range(150))
    chunks = chunk_file("script.py", src)
    assert chunks and all(c.kind == "window" for c in chunks)
    assert chunks[0].start_line == 1


def test_unknown_language_falls_back():
    chunks = chunk_file("notes.txt", "hello\nworld\n")
    assert chunks and chunks[0].kind == "window"
    assert chunks[0].language == "text"


def test_javascript_functions():
    src = (
        "function add(a, b) {\n  return a + b;\n}\n\n"
        "class Svc {\n  run() { return 1; }\n}\n"
    )
    chunks = chunk_file("a.js", src)
    names = {c.name for c in chunks}
    assert "add" in names
    assert "Svc" in names


def test_pathologically_deep_nesting_falls_back_to_windows():
    # ~3000 nested ifs would overflow the recursive descent at Python's default
    # recursion limit; one such (generated) file must not abort the index.
    src = "def f():\n"
    for i in range(3000):
        src += "    " * (i + 1) + "if True:\n"
    src += "    " * 3001 + "return 1\n"
    chunks = chunk_file("deep.py", src)  # must not raise RecursionError
    assert chunks, "deep file should still yield window chunks, not crash"
    assert all(c.kind == "window" for c in chunks)


def test_unicode_identifiers_are_named():
    src = "def café(número):\n    return número\n\nclass Ångström:\n    pass\n"
    names = {c.name for c in chunk_file("uni.py", src)}
    assert "café" in names and "Ångström" in names


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
