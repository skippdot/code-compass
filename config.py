"""Load .env into os.environ on import, with zero dependencies.

The Voyage client reads VOYAGE_API_KEY straight from the environment, so we
just need the key present before VoyageEmbedder() is constructed. Importing
this module anywhere early does that.
"""

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    p = Path(__file__).parent / path
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env()
