"""Index/search tegen het echte EmbeddingGemma 2 q4-ONNX-bestand (niet hash).

Download is ~175 MB, eenmalig naar de HF-cache. Skip als er geen netwerk is.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from kippiesearch.config import Settings
from kippiesearch.embeddings import OnnxGemmaEmbedder
from kippiesearch.indexer import Indexer
from kippiesearch.search import SearchService
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore

def _load_real_embedder(tmp_path: Path) -> OnnxGemmaEmbedder:
    if os.environ.get("KIPPIESEARCH_TEST_ONNX", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("opt-in: KIPPIESEARCH_TEST_ONNX=1 (download ~175 MB q4-ONNX)")
    settings = Settings(
        data_dir=tmp_path / "data",
        embedder="onnx",
        embedding_model_id="onnx-community/embeddinggemma-2-ONNX",
        embedding_onnx_file="onnx/model_q4.onnx",
        embedding_dim=256,
        max_seq_length=64,
        onnx_threads=1,
        auth_disabled=True,
    )
    try:
        return OnnxGemmaEmbedder(
            model_dir=settings.model_dir,
            model_id=settings.embedding_model_id,
            onnx_file=settings.embedding_onnx_file,
            dim=settings.embedding_dim,
            max_seq_length=settings.max_seq_length,
            threads=settings.onnx_threads,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"echt ONNX-model niet beschikbaar: {exc}")


def test_real_q4_onnx_encodes_without_multimodal_error(tmp_path: Path):
    embedder = _load_real_embedder(tmp_path)
    vectors = embedder.embed_queries(["rode planeet Mars"])
    assert vectors.shape == (1, 256)
    assert np.isfinite(vectors).all()
    norm = float(np.linalg.norm(vectors[0]))
    assert 0.99 <= norm <= 1.01
    docs = embedder.embed_documents(
        ["Mars is de rode planeet.", "Pasta carbonara zonder room."],
        titles=["planeten", "recept"],
    )
    assert docs.shape == (2, 256)
    assert "image_features" in embedder._input_names
    assert "video_features" in embedder._input_names
    assert "audio_features" in embedder._input_names
    assert embedder.info()["onnx_file"] == "onnx/model_q4.onnx"


def test_real_q4_onnx_index_and_search(tmp_path: Path):
    embedder = _load_real_embedder(tmp_path)
    share = tmp_path / "share"
    share.mkdir()
    (share / "planeten.md").write_text(
        "Mars is de rode planeet. Olympus Mons is de hoogste vulkaan.\n",
        encoding="utf-8",
    )
    (share / "recept.txt").write_text(
        "Pasta carbonara: eieren, pecorino, guanciale en zwarte peper.\n",
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        nas_roots=[str(share)],
        embedder="onnx",
        embedding_dim=256,
        throttle_s=0,
        batch_size=2,
        chunk_chars=400,
        chat_model="mock",
        auth_disabled=True,
    )
    store = VectorStore(settings.db_dir, dim=settings.embedding_dim)
    result = Indexer(settings, store, embedder, AppStatus()).run(full=True)
    assert result["errors"] == 0
    assert result["indexed"] == 2
    hits = SearchService(store, embedder).search("rode planeet Mars", top_k=3)
    assert hits
    assert "planeten" in hits[0]["path"]
