#!/usr/bin/env python3
"""Kleine RAM/snelheidsmeting op de huidige machine (niet de Pi)."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import psutil

from kippiesearch.config import Settings
from kippiesearch.embeddings import HashEmbedder
from kippiesearch.indexer import Indexer
from kippiesearch.search import SearchService
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


def main() -> None:
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    tmp = Path(tempfile.mkdtemp(prefix="kippiesearch-bench-"))
    share = tmp / "share"
    shutil.copytree(CORPUS, share)
    extra = share / "nas"
    extra.mkdir(exist_ok=True)
    for i in range(40):
        (extra / f"doc-{i:02d}.md").write_text(
            f"# Document {i}\nMars, pasta, valversnelling en code-voorbeelden. " * 40,
            encoding="utf-8",
        )
    settings = Settings(
        data_dir=tmp / "data",
        nas_roots=[str(share)],
        embedder="hash",
        embedding_dim=256,
        throttle_s=0,
        batch_size=8,
        chunk_chars=800,
        auth_disabled=True,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = VectorStore(settings.db_dir, dim=settings.embedding_dim)
    embedder = HashEmbedder(dim=settings.embedding_dim)
    indexer = Indexer(settings, store, embedder, AppStatus())
    t0 = time.perf_counter()
    result = indexer.run(full=True)
    index_s = time.perf_counter() - t0
    rss1 = proc.memory_info().rss
    searcher = SearchService(store, embedder)
    t1 = time.perf_counter()
    hits = searcher.search("rode planeet Mars", top_k=5)
    search_ms = (time.perf_counter() - t1) * 1000
    rss2 = proc.memory_info().rss
    print("=== KippieSearch benchmark (hash-embedder, deze VM) ===")
    print(f"bestanden geïndexeerd: {result}")
    print(f"index-tijd:            {index_s:.3f}s")
    print(f"zoek-tijd:             {search_ms:.1f}ms  (hits={len(hits)})")
    print(f"RSS start:             {rss0 / 1024 / 1024:.1f} MB")
    print(f"RSS na index:          {rss1 / 1024 / 1024:.1f} MB")
    print(f"RSS na search:         {rss2 / 1024 / 1024:.1f} MB")
    print()
    print("Verwachting Raspberry Pi 5 (8 GB, aarch64, naast HA):")
    print("- Hash-embedder (tests):   ~80–150 MB RSS")
    print("- EmbeddingGemma 2 q4 ONNX: model ~174 MB op schijf, sessie ~250–450 MB extra")
    print("- Totaal proces met model:  ~450–900 MB, piek bij eerste download hoger")
    print("- Indexeren PDF/DOCX:      enkele tot tientallen bestanden/minuut (CPU, throttle)")
    print("- Query:                   tens tot low-hundreds ms na warmup")
    print("- Laat batch_size=4, onnx_threads=2, max_seq_length=512, throttle_s=0.05")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
