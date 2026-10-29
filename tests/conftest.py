from __future__ import annotations

from pathlib import Path

import pytest

from kippiesearch.config import Settings
from kippiesearch.embeddings import HashEmbedder
from kippiesearch.indexer import Indexer
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "corpus"


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    nas = tmp_path / "share"
    nas.mkdir()
    data.mkdir()
    return Settings(
        data_dir=data,
        nas_roots=[str(nas)],
        embedder="hash",
        embedding_dim=64,
        api_token="",
        auth_disabled=True,
        throttle_s=0,
        batch_size=2,
        chunk_chars=400,
        chunk_overlap=40,
        itslearning_enabled=False,
        nas_smb_enabled=False,
        log_level="warning",
        chat_enabled=True,
        chat_model="mock",
    )


@pytest.fixture()
def indexed_app(tmp_settings: Settings, tmp_path: Path):
    share = Path(tmp_settings.nas_roots[0])
    school = tmp_settings.itslearning_dir
    school.mkdir(parents=True, exist_ok=True)
    (share / "planeten.md").write_text(
        "Mars is de rode planeet. De Olympus Mons is de hoogste vulkaan in het zonnestelsel.\n",
        encoding="utf-8",
    )
    (share / "recept.txt").write_text(
        "Pasta carbonara: eieren, pecorino, guanciale en zwarte peper.\n",
        encoding="utf-8",
    )
    (share / "hello.py").write_text(
        "def greet(name: str) -> str:\n    return f'hallo {name}'\n",
        encoding="utf-8",
    )
    (school / "opdracht.md").write_text(
        "Opdracht natuurkunde: bereken de valversnelling op Mars.\n",
        encoding="utf-8",
    )
    store = VectorStore(tmp_settings.db_dir, dim=tmp_settings.embedding_dim)
    embedder = HashEmbedder(dim=tmp_settings.embedding_dim)
    status = AppStatus()
    indexer = Indexer(tmp_settings, store, embedder, status)
    indexer.run(full=True)
    return tmp_settings, store, embedder, indexer, status
