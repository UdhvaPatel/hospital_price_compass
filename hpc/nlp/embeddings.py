"""Text embeddings from NVIDIA NIM, cached on disk so each text is embedded only once.

Cache: data/cache/embeddings/<model>.parquet (text, input_type, vector). Vectors are stored
L2-normalised, so cosine similarity is a plain dot product.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd
import requests

from hpc.config import DATA_DIR

CACHE_DIR = DATA_DIR / "cache" / "embeddings"


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n == 0, 1, n)


class EmbeddingCache:
    def __init__(self, model: str, cache_dir: Path = CACHE_DIR):
        self.path = Path(cache_dir) / (re.sub(r"[^A-Za-z0-9._-]+", "_", model) + ".parquet")
        self.vectors: dict[tuple[str, str], np.ndarray] = {}
        self._dirty = False
        if self.path.exists():
            df = pd.read_parquet(self.path)
            for t, it, v in zip(df["text"], df["input_type"], df["vector"]):
                self.vectors[(t, it)] = np.asarray(v, dtype=np.float32)

    def get(self, text: str, input_type: str) -> np.ndarray | None:
        return self.vectors.get((text, input_type))

    def put(self, text: str, input_type: str, vector: np.ndarray) -> None:
        self.vectors[(text, input_type)] = vector.astype(np.float32)
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(self.vectors)
        pd.DataFrame({
            "text": [k[0] for k in keys],
            "input_type": [k[1] for k in keys],
            "vector": [self.vectors[k].tolist() for k in keys],
        }).to_parquet(self.path, index=False)
        self._dirty = False


class Embedder:
    """embed(texts) -> (n, d) array of unit vectors. `backend` can be swapped in tests."""

    def __init__(self, client=None, model: str | None = None, batch_size: int = 100,
                 backend: Callable[[list[str], str], list[list[float]]] | None = None,
                 cache: EmbeddingCache | None = None):
        if backend is None:
            from hpc.parse.nim import NimClient
            client = client or NimClient()
            model = model or client.embed_model
            backend = self._nim_backend(client, model)
        self.model = model or "test"
        self.backend = backend
        self.batch_size = batch_size
        self.cache = cache if cache is not None else EmbeddingCache(self.model)
        self.calls = 0

    @staticmethod
    def _nim_backend(client, model):
        def call(texts: list[str], input_type: str) -> list[list[float]]:
            for attempt in range(5):
                r = requests.post(
                    f"{client.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {client.api_key}", "Accept": "application/json"},
                    json={"model": model, "input": texts, "input_type": input_type, "truncate": "END"},
                    timeout=client.timeout,
                )
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt * 2)
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"embedding HTTP {r.status_code}: {r.text[:200]}")
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
            raise RuntimeError("embedding retries exhausted")
        return call

    def embed(self, texts: Sequence[str], input_type: str = "query") -> np.ndarray:
        texts = [t if t.strip() else "(blank)" for t in texts]
        missing = list(dict.fromkeys(t for t in texts if self.cache.get(t, input_type) is None))
        for i in range(0, len(missing), self.batch_size):
            batch = missing[i:i + self.batch_size]
            vecs = _normalise(np.asarray(self.backend(batch, input_type), dtype=np.float32))
            self.calls += 1
            for t, v in zip(batch, vecs):
                self.cache.put(t, input_type, v)
        if missing:
            self.cache.save()
        return np.stack([self.cache.get(t, input_type) for t in texts]) if texts else np.zeros((0, 0))


def best_matches(query_vecs: np.ndarray, target_vecs: np.ndarray, target_labels: Sequence[str]
                 ) -> list[tuple[str, float, str, float]]:
    """For each query: (best label, best score, runner-up label, runner-up score). Several
    target vectors may share a label (aliases); a label's score is its best alias."""
    sims = query_vecs @ target_vecs.T
    labels = np.asarray(target_labels)
    uniq = list(dict.fromkeys(target_labels))
    per_label = np.stack([sims[:, labels == u].max(axis=1) for u in uniq], axis=1)
    order = np.argsort(-per_label, axis=1)
    out = []
    for r in range(per_label.shape[0]):
        b = order[r, 0]
        s = order[r, 1] if len(uniq) > 1 else b
        out.append((uniq[b], float(per_label[r, b]), uniq[s], float(per_label[r, s]) if len(uniq) > 1 else 0.0))
    return out
