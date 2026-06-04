"""Embedding layer for the context engine.

Everything sits behind the `Embedder` interface so the rest of the system
(indexer, retriever) never imports a vendor SDK directly. Swapping Voyage for a
local Ollama model later means writing one new subclass — nothing else changes.

Key retrieval detail: code-tuned embedders distinguish the *role* of the text.
A stored code chunk is a "document"; a user's natural-language search is a
"query". Voyage exposes this via `input_type`, and using the right one
measurably improves recall, because query and document vectors are nudged into
a shared space during the model's contrastive training. We honor that split
with two public methods instead of one.
"""

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator


class Embedder(ABC):
    """Vendor-agnostic embedding contract."""

    #: dimensionality of the produced vectors; the store needs it up front
    dim: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed code chunks for storage (input_type='document')."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query (input_type='query')."""


class VoyageEmbedder(Embedder):
    """`voyage-code-3` via the official client.

    Voyage caps a single request at MAX_BATCH inputs *and* MAX_BATCH_TOKENS
    total tokens, so large repos must be split into batches that respect both
    limits. We pack chunks greedily into batches in `_make_batches` (the part
    you'll write), then ship each batch through `_embed_batch`.
    """

    MAX_BATCH = 128            # Voyage hard cap on inputs per request
    MAX_BATCH_TOKENS = 120_000  # Voyage hard cap on total tokens per request

    MAX_RETRIES = 6      # attempts on rate-limit before giving up
    BASE_DELAY = 2.0     # seconds; doubled each retry (2, 4, 8, ... + jitter)

    def __init__(self, model: str = "voyage-code-3", dim: int = 1024):
        import voyageai  # imported lazily so the module loads without the dep

        self.client = voyageai.Client()  # reads VOYAGE_API_KEY from env
        self.model = model
        self.dim = dim

    def _est_tokens(self, text: str) -> int:
        """Token count for batch packing.

        Uses Voyage's real (local) tokenizer when a client is present — code is
        token-denser than the ~4-chars/token rule of thumb, which underestimates
        and lets batches blow past the 120k cap. Falls back to the heuristic
        only when there's no client (offline unit tests)."""
        client = getattr(self, "client", None)
        if client is not None:
            return client.count_tokens([text], self.model)
        return max(1, len(text) // 4)

    def _embed_batch(self, texts: list[str], input_type: str) -> list[list[float]]:
        """One API call for a pre-sized batch, with backoff on rate limits.

        Free-tier (and TPM) limits surface as RateLimitError; we wait and retry
        with exponential backoff + jitter rather than crash a long index run.
        """
        from voyageai.error import RateLimitError

        for attempt in range(self.MAX_RETRIES):
            try:
                result = self.client.embed(
                    texts, model=self.model, input_type=input_type
                )
                return result.embeddings
            except RateLimitError:
                if attempt == self.MAX_RETRIES - 1:
                    raise
                delay = self.BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                print(f"  rate-limited, retrying in {delay:.1f}s "
                      f"(attempt {attempt + 1}/{self.MAX_RETRIES})")
                time.sleep(delay)
        raise RuntimeError("unreachable")  # loop either returns or raises

    def _make_batches(self, texts: list[str]) -> Iterator[list[str]]:
        """Yield sublists of `texts`, each within MAX_BATCH inputs and
        MAX_BATCH_TOKENS total estimated tokens.

        TODO(human): implement the greedy packing loop. Walk `texts` once,
        accumulating into a current batch; flush (yield) the batch when adding
        the next chunk would exceed either MAX_BATCH (count) or
        MAX_BATCH_TOKENS (sum of self._est_tokens). Don't forget the final
        partial batch. Edge case to decide on: a single chunk whose own token
        estimate already exceeds MAX_BATCH_TOKENS — yield it alone (the API
        truncates it) rather than dropping or infinite-looping on it.
        """
        batch: list[str] = []
        batch_tokens = 0
        for text in texts:
            tokens = self._est_tokens(text)
            fits = (
                len(batch) < self.MAX_BATCH
                and batch_tokens + tokens <= self.MAX_BATCH_TOKENS
            )
            if fits:
                batch.append(text)
                batch_tokens += tokens
            else:
                if batch:  # guard: first chunk may already be oversized
                    yield batch
                batch = [text]
                batch_tokens = tokens

        if batch:
            yield batch

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in self._make_batches(texts):
            out.extend(self._embed_batch(batch, input_type="document"))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text], input_type="query")[0]


class LocalEmbedder(Embedder):
    """Offline alternative via sentence-transformers — no API, no rate limits.

    Default model is Qwen3-Embedding-0.6B: code-aware, MPS-friendly, 1024-dim
    (same as voyage-code-3, so vectors are dimensionally drop-in). Like Voyage,
    it is instruction-aware — queries get a task-instruction prompt, documents
    do not — which we map onto the same two-method contract.
    """

    def __init__(self, model: str = "Qwen/Qwen3-Embedding-0.6B"):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)  # auto-selects MPS/CUDA/CPU
        # method was renamed across sentence-transformers versions
        get_dim = getattr(self.model, "get_embedding_dimension", None) or \
            self.model.get_sentence_embedding_dimension
        self.dim = get_dim()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        vec = self.model.encode(
            [text], prompt_name="query", normalize_embeddings=True
        )[0]
        return vec.tolist()
