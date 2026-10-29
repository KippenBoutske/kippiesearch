"""Configuratie: HA add-on options.json, environment, defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


class Settings(BaseModel):
    """Runtime settings. Secrets komen uit HA-opties of environment, nooit uit code."""

    host: str = "0.0.0.0"
    port: int = 8765
    data_dir: Path = Path("/data")
    log_level: str = "info"

    api_token: str = ""
    auth_disabled: bool = False

    # NAS via HA netwerkopslag (voorkeur) en/of SMB-fallback
    nas_roots: list[str] = Field(default_factory=lambda: ["/share", "/media"])
    nas_smb_enabled: bool = False
    nas_smb_host: str = ""
    nas_smb_shares: list[str] = Field(default_factory=list)
    nas_smb_username: str = ""
    nas_smb_password: str = ""
    nas_smb_port: int = 445

    # it's learning — URL/school leeg tot de gebruiker ze invult
    itslearning_enabled: bool = False
    itslearning_url: str = ""
    itslearning_organisation: str = ""
    itslearning_username: str = ""
    itslearning_password: str = ""
    itslearning_rate_limit_s: float = 0.75

    # Embeddings — EmbeddingGemma 2 text-only (270M), q4 ONNX ~174 MB
    embedder: str = "onnx"  # onnx | hash | sentence-transformers
    embedding_model_id: str = "onnx-community/embeddinggemma-2-ONNX"
    embedding_onnx_file: str = "onnx/model_q4.onnx"
    embedding_dim: int = 256  # MRL-truncatie; 256 behoudt vrijwel volle tekstkwaliteit
    max_seq_length: int = 512
    onnx_threads: int = 2

    # Indexing / Pi-tuning
    chunk_chars: int = 1800
    chunk_overlap: int = 220
    batch_size: int = 4
    index_threads: int = 2
    max_file_mb: float = 32.0
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            "**/.git/**",
            "**/node_modules/**",
            "**/.DS_Store",
            "**/Thumbs.db",
            "**/*.tmp",
            "**/*.iso",
            "**/*.mkv",
            "**/*.mp4",
            "**/*.mp3",
            "**/@eaDir/**",
            "**/#recycle/**",
        ]
    )
    throttle_s: float = 0.05
    ocr_enabled: bool = False
    rescan_hours: float = 6.0
    itslearning_cron: str = "0 3 * * *"
    nas_cron: str = "0 */6 * * *"

    @field_validator(
        "nas_roots",
        "nas_smb_shares",
        "exclude_globs",
        mode="before",
    )
    @classmethod
    def _csv_lists(cls, value: Any) -> list[str]:
        return _split_csv(value)

    @field_validator("log_level", mode="before")
    @classmethod
    def _level(cls, value: Any) -> str:
        return str(value or "info").lower()

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "lancedb"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def itslearning_dir(self) -> Path:
        return self.data_dir / "itslearning"

    @property
    def nas_cache_dir(self) -> Path:
        return self.data_dir / "nas-cache"

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_mb * 1024 * 1024)

    def require_auth(self) -> bool:
        return bool(self.api_token) and not self.auth_disabled

    def nas_smb_ready(self) -> bool:
        return bool(
            self.nas_smb_enabled
            and str(self.nas_smb_host or "").strip()
            and self.nas_smb_shares
            and str(self.nas_smb_username or "").strip()
        )

    def nas_smb_skip_reason(self) -> str:
        if not self.nas_smb_enabled:
            return "SMB-fallback uitgeschakeld"
        missing: list[str] = []
        if not str(self.nas_smb_host or "").strip():
            missing.append("host")
        if not self.nas_smb_shares:
            missing.append("share")
        if not str(self.nas_smb_username or "").strip():
            missing.append("gebruikersnaam")
        if missing:
            return "SMB-fallback overgeslagen: " + ", ".join(missing) + " ontbreekt"
        return ""

    def itslearning_ready(self) -> bool:
        return bool(
            self.itslearning_enabled
            and str(self.itslearning_url or "").strip()
            and str(self.itslearning_username or "").strip()
            and str(self.itslearning_password or "").strip()
        )

    def itslearning_skip_reason(self) -> str:
        if not self.itslearning_enabled:
            return "it's learning-sync uitgeschakeld"
        missing: list[str] = []
        if not str(self.itslearning_url or "").strip():
            missing.append("URL")
        if not str(self.itslearning_username or "").strip():
            missing.append("gebruikersnaam")
        if not str(self.itslearning_password or "").strip():
            missing.append("wachtwoord")
        if missing:
            return "it's learning overgeslagen: " + ", ".join(missing) + " ontbreekt"
        return ""


def _load_options_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _env_overrides() -> dict[str, Any]:
    mapping = {
        "KIPPIESEARCH_HOST": "host",
        "KIPPIESEARCH_PORT": "port",
        "KIPPIESEARCH_DATA_DIR": "data_dir",
        "KIPPIESEARCH_LOG_LEVEL": "log_level",
        "KIPPIESEARCH_API_TOKEN": "api_token",
        "KIPPIESEARCH_EMBEDDER": "embedder",
        "KIPPIESEARCH_EMBEDDING_DIM": "embedding_dim",
        "KIPPIESEARCH_NAS_ROOTS": "nas_roots",
        "KIPPIESEARCH_NAS_SMB_ENABLED": "nas_smb_enabled",
        "KIPPIESEARCH_NAS_SMB_HOST": "nas_smb_host",
        "KIPPIESEARCH_NAS_SMB_SHARES": "nas_smb_shares",
        "KIPPIESEARCH_NAS_SMB_USERNAME": "nas_smb_username",
        "KIPPIESEARCH_NAS_SMB_PASSWORD": "nas_smb_password",
        "KIPPIESEARCH_ITSLEARNING_ENABLED": "itslearning_enabled",
        "KIPPIESEARCH_ITSLEARNING_URL": "itslearning_url",
        "KIPPIESEARCH_ITSLEARNING_USERNAME": "itslearning_username",
        "KIPPIESEARCH_ITSLEARNING_PASSWORD": "itslearning_password",
        "KIPPIESEARCH_OCR_ENABLED": "ocr_enabled",
        "KIPPIESEARCH_BATCH_SIZE": "batch_size",
        "KIPPIESEARCH_ONNX_THREADS": "onnx_threads",
        "KIPPIESEARCH_MAX_FILE_MB": "max_file_mb",
        "KIPPIESEARCH_THROTTLE_S": "throttle_s",
        "KIPPIESEARCH_EXCLUDE_GLOBS": "exclude_globs",
        "KIPPIESEARCH_AUTH_DISABLED": "auth_disabled",
    }
    truthy = {"1", "true", "yes", "on"}
    bool_fields = {
        "nas_smb_enabled",
        "itslearning_enabled",
        "ocr_enabled",
        "auth_disabled",
    }
    int_fields = {"port", "embedding_dim", "batch_size", "onnx_threads"}
    float_fields = {"max_file_mb", "throttle_s"}
    out: dict[str, Any] = {}
    for env_name, field in mapping.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        if field in bool_fields:
            out[field] = raw.lower() in truthy
        elif field in int_fields:
            out[field] = int(raw)
        elif field in float_fields:
            out[field] = float(raw)
        else:
            out[field] = raw
    return out


def load_settings(
    options_path: Path | None = None,
    data_dir: Path | None = None,
) -> Settings:
    options_file = options_path or Path(
        os.environ.get("KIPPIESEARCH_OPTIONS", "/data/options.json")
    )
    payload: dict[str, Any] = {}
    payload.update(_load_options_json(options_file))
    payload.update(_env_overrides())
    if data_dir is not None:
        payload["data_dir"] = data_dir
    elif "data_dir" not in payload:
        payload["data_dir"] = os.environ.get("KIPPIESEARCH_DATA_DIR", "/data")
    settings = Settings.model_validate(payload)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.itslearning_dir.mkdir(parents=True, exist_ok=True)
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    return settings
