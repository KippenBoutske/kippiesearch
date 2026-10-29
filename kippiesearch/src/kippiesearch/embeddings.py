"""Embeddings: EmbeddingGemma 2 (ONNX q4) met hash-fallback voor tests/Pi-zonder-model.

EmbeddingGemma 2 (okt 2026) is kleiner in de text-backbone (270M vs 300M) en
beter op code. We laden alleen de text-encoder (q4 ~174 MB). Vision/audio blijven
uit vanwege RAM op de Pi 5.

Prompts (officieel):
  query:    task: search result | query: {content}
  document: title: {title|none} | text: {content}
MRL: native 768-d, wij truncaten naar embedding_dim en her-normaliseren.
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

from kippiesearch.logging_setup import get_logger

log = get_logger("embeddings")

QUERY_PREFIX = "task: search result | query: "
DOCUMENT_PREFIX = "title: {title} | text: "


class Embedder(Protocol):
    dim: int

    def embed_queries(self, texts: list[str]) -> np.ndarray: ...

    def embed_documents(
        self, texts: list[str], titles: list[str] | None = None
    ) -> np.ndarray: ...

    def info(self) -> dict: ...


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return (vectors / norms).astype(np.float32)


def _truncate_mrl(vectors: np.ndarray, dim: int) -> np.ndarray:
    if vectors.shape[1] == dim:
        return _l2_normalize(vectors.astype(np.float32))
    sliced = vectors[:, :dim]
    return _l2_normalize(sliced.astype(np.float32))


class HashEmbedder:
    """Deterministische bag-of-words embedding. Alleen voor tests en no-model modus."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model_id = "hash://token-bow"

    def info(self) -> dict:
        return {
            "backend": "hash",
            "model": self.model_id,
            "dim": self.dim,
            "offline": True,
        }

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._embed(QUERY_PREFIX + t) for t in texts])

    def embed_documents(
        self, texts: list[str], titles: list[str] | None = None
    ) -> np.ndarray:
        titles = titles or ["none"] * len(texts)
        rows = []
        for text, title in zip(texts, titles, strict=False):
            prefix = DOCUMENT_PREFIX.format(title=title or "none")
            rows.append(self._embed(prefix + text))
        return np.stack(rows) if rows else np.zeros((0, self.dim), dtype=np.float32)

    def _embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec.reshape(1, -1))[0]


class OnnxGemmaEmbedder:
    """EmbeddingGemma 2 text-only via ONNX Runtime (geen PyTorch)."""

    def __init__(
        self,
        model_dir: Path,
        model_id: str = "onnx-community/embeddinggemma-2-ONNX",
        onnx_file: str = "onnx/model_q4.onnx",
        dim: int = 256,
        max_seq_length: int = 512,
        threads: int = 2,
    ) -> None:
        self.model_id = model_id
        self.onnx_file = onnx_file
        self.dim = dim
        self.max_seq_length = max_seq_length
        self.threads = threads
        self.model_dir = Path(model_dir)
        self._session = None
        self._tokenizer = None
        self._input_names: list[str] = []
        self._output_name = "sentence_embedding"
        self._lock = threading.Lock()
        self._load()

    def info(self) -> dict:
        return {
            "backend": "onnx",
            "model": self.model_id,
            "onnx_file": self.onnx_file,
            "dim": self.dim,
            "max_seq_length": self.max_seq_length,
            "threads": self.threads,
            "offline": True,
        }

    def _download(self) -> Path:
        from huggingface_hub import hf_hub_download

        self.model_dir.mkdir(parents=True, exist_ok=True)
        graph = hf_hub_download(
            repo_id=self.model_id,
            filename=self.onnx_file,
            local_dir=str(self.model_dir),
        )
        data_file = self.onnx_file + "_data"
        try:
            hf_hub_download(
                repo_id=self.model_id,
                filename=data_file,
                local_dir=str(self.model_dir),
            )
        except Exception:
            log.info("onnx_data_optional_missing", file=data_file)
        for extra in ("tokenizer.json", "tokenizer_config.json", "config.json"):
            try:
                hf_hub_download(
                    repo_id=self.model_id,
                    filename=extra,
                    local_dir=str(self.model_dir),
                )
            except Exception:
                log.info("tokenizer_extra_optional", file=extra)
        return Path(graph)

    def _load(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        graph_path = self._download()
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, self.threads)
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(graph_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = [inp.name for inp in self._session.get_inputs()]
        outputs = [out.name for out in self._session.get_outputs()]
        if "sentence_embedding" in outputs:
            self._output_name = "sentence_embedding"
        else:
            self._output_name = outputs[0]

        tokenizer_path = self.model_dir / "tokenizer.json"
        if not tokenizer_path.is_file():
            nested = list(self.model_dir.rglob("tokenizer.json"))
            if nested:
                tokenizer_path = nested[0]
        if not tokenizer_path.is_file():
            raise FileNotFoundError("tokenizer.json ontbreekt voor EmbeddingGemma 2")
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=self.max_seq_length)
        self._tokenizer.enable_padding(length=self.max_seq_length)
        log.info("onnx_embedder_ready", model=self.model_id, file=self.onnx_file)

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        prefixed = [QUERY_PREFIX + (t or "") for t in texts]
        return self._encode(prefixed)

    def embed_documents(
        self, texts: list[str], titles: list[str] | None = None
    ) -> np.ndarray:
        titles = titles or ["none"] * len(texts)
        prefixed = []
        for text, title in zip(texts, titles, strict=False):
            prefixed.append(DOCUMENT_PREFIX.format(title=title or "none") + (text or ""))
        return self._encode(prefixed)

    def _encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        with self._lock:
            encodings = self._tokenizer.encode_batch(texts)
            input_ids = np.vstack([np.array(enc.ids, dtype=np.int64) for enc in encodings])
            attention = np.vstack(
                [np.array(enc.attention_mask, dtype=np.int64) for enc in encodings]
            )
            feed: dict[str, np.ndarray] = {}
            for name in self._input_names:
                lowered = name.lower()
                if "mask" in lowered:
                    feed[name] = attention
                elif "position" in lowered:
                    feed[name] = np.broadcast_to(
                        np.arange(input_ids.shape[1], dtype=np.int64),
                        input_ids.shape,
                    ).copy()
                elif lowered in {"input_ids", "inputs", "input_id"} or "input" in lowered:
                    feed[name] = input_ids
            outputs = self._session.run([self._output_name], feed)[0]
        vectors = np.asarray(outputs)
        if vectors.ndim == 3:
            # fallback mean-pool als de export geen sentence_embedding heeft
            mask = attention[:, :, None]
            summed = (vectors * mask).sum(axis=1)
            counts = np.maximum(mask.sum(axis=1), 1)
            vectors = summed / counts
        return _truncate_mrl(vectors, self.dim)


def create_embedder(settings) -> Embedder:
    kind = (settings.embedder or "onnx").lower()
    if kind in {"hash", "test", "dummy"}:
        log.info("using_hash_embedder")
        return HashEmbedder(dim=settings.embedding_dim)
    if kind in {"sentence-transformers", "st", "fallback"}:
        try:
            return _sentence_transformers_embedder(settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("sentence_transformers_failed", error=str(exc))
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
        log.warning("onnx_embedder_failed_fallback_hash", error=str(exc))
        return HashEmbedder(dim=settings.embedding_dim)


def _sentence_transformers_embedder(settings) -> Embedder:
    from sentence_transformers import SentenceTransformer

    class STEmbedder:
        def __init__(self) -> None:
            self.dim = settings.embedding_dim
            self.model_id = "google/embeddinggemma-300m"
            self._model = SentenceTransformer(
                self.model_id, cache_folder=str(settings.model_dir)
            )

        def info(self) -> dict:
            return {
                "backend": "sentence-transformers",
                "model": self.model_id,
                "dim": self.dim,
            }

        def embed_queries(self, texts: list[str]) -> np.ndarray:
            vectors = np.asarray(self._model.encode_query(texts), dtype=np.float32)
            return _truncate_mrl(vectors, self.dim)

        def embed_documents(
            self, texts: list[str], titles: list[str] | None = None
        ) -> np.ndarray:
            vectors = np.asarray(self._model.encode_document(texts), dtype=np.float32)
            return _truncate_mrl(vectors, self.dim)

    return STEmbedder()
