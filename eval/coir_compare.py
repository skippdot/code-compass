"""Benchmark embedders on CoIR (standardized code-IR), one model per run.

Lets us put a number on "is voyage-code-3 actually better than the open-source
CodeRankEmbed for our NL->code use case" before investing in a model swap.

Run:  python eval/coir_compare.py voyage   [task]
      python eval/coir_compare.py coderank [task]   (default task: cosqa)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coir  # noqa: E402
import numpy as np  # noqa: E402
from coir.evaluation import COIR  # noqa: E402

import config  # noqa: E402,F401  -- loads .env

HERE = os.path.dirname(os.path.abspath(__file__))


def _text(doc) -> str:
    if isinstance(doc, dict):
        return (doc.get("title", "") + "\n" + doc.get("text", "")).strip()
    return str(doc)


class VoyageModel:
    """voyage-code-3 via our own VoyageEmbedder (document/query input_type)."""

    def __init__(self):
        from embedder import VoyageEmbedder
        self.e = VoyageEmbedder()

    def encode_queries(self, queries, batch_size=64, **kw):
        return np.array([self.e.embed_query(q) for q in queries])

    def encode_corpus(self, corpus, batch_size=64, **kw):
        return np.array(self.e.embed_documents([_text(d) for d in corpus]))


class CodeRankEmbedModel:
    """nomic-ai/CodeRankEmbed (137M) — requires a query instruction prefix."""

    QUERY_PREFIX = "Represent this query for searching relevant code: "

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.m = SentenceTransformer("nomic-ai/CodeRankEmbed", trust_remote_code=True)
        # cap context: the model defaults to 8192, and seq^2 attention on long
        # docs blows up MPS memory (31GB buffer). 512 is plenty for code chunks.
        self.m.max_seq_length = 512

    def encode_queries(self, queries, batch_size=64, **kw):
        return self.m.encode([self.QUERY_PREFIX + q for q in queries],
                             batch_size=batch_size, normalize_embeddings=True)

    def encode_corpus(self, corpus, batch_size=64, **kw):
        return self.m.encode([_text(d) for d in corpus],
                             batch_size=batch_size, normalize_embeddings=True)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "coderank"
    task = sys.argv[2] if len(sys.argv) > 2 else "cosqa"
    model = VoyageModel() if which == "voyage" else CodeRankEmbedModel()

    tasks = coir.get_tasks(tasks=[task])
    evaluation = COIR(tasks=tasks, batch_size=64)
    results = evaluation.run(model, output_folder=os.path.join(HERE, "coir_out", which))
    print(f"\n=== {which} on {task} ===")
    print(results)


if __name__ == "__main__":
    main()
