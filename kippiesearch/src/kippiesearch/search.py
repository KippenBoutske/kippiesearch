"""Semantic search + snippet highlighting."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import numpy as np

from kippiesearch.embeddings import Embedder
from kippiesearch.store import VectorStore

_TOKEN = re.compile(r"\w+", re.UNICODE)


def _parse_date(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        if len(text) <= 10:
            dt = datetime.fromisoformat(text)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


def highlight_snippet(text: str, query: str, radius: int = 180) -> str:
    if not text:
        return ""
    tokens = [t.lower() for t in _TOKEN.findall(query) if len(t) > 1]
    lower = text.lower()
    hit = -1
    matched = ""
    for token in tokens:
        pos = lower.find(token.lower())
        if pos >= 0:
            hit = pos
            matched = token
            break
    if hit < 0:
        snippet = text[: radius * 2]
        suffix = "…" if len(text) > len(snippet) else ""
        return html.escape(snippet) + suffix
    start = max(0, hit - radius)
    end = min(len(text), hit + len(matched) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    piece = text[start:end]
    pattern = re.compile("|".join(re.escape(t) for t in tokens), re.IGNORECASE)
    highlighted = pattern.sub(lambda m: f"<mark>{html.escape(m.group(0))}</mark>", html.escape(piece))
    return prefix + highlighted + suffix


def _score_from_row(row: dict[str, Any]) -> float:
    if "_distance" in row:
        distance = float(row["_distance"])
        # cosine distance ~ 1 - cosine similarity in veel LanceDB-versies
        return round(1.0 - distance, 4)
    if "_score" in row:
        return round(float(row["_score"]), 4)
    if "score" in row:
        return round(float(row["score"]), 4)
    return 0.0


class SearchService:
    def __init__(self, store: VectorStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    def search(
        self,
        query: str,
        top_k: int = 8,
        source: str | None = None,
        file_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        vector = self.embedder.embed_queries([query])[0]
        rows = self.store.search(
            np.asarray(vector, dtype=np.float32),
            top_k=max(1, min(int(top_k), 50)),
            source=source or None,
            file_type=file_type or None,
            after=_parse_date(after),
            before=_parse_date(before),
        )
        results = []
        for row in rows:
            text = str(row.get("text") or "")
            results.append(
                {
                    "path": row.get("path"),
                    "snippet": highlight_snippet(text, query),
                    "text": text[:1200],
                    "score": _score_from_row(row),
                    "source": row.get("source"),
                    "file_type": row.get("file_type"),
                    "mtime": row.get("mtime"),
                    "size": row.get("size"),
                    "chunk_index": row.get("chunk_index"),
                    "title": row.get("title"),
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results
