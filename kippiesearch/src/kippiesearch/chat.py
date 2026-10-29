"""RAG-chat: EmbeddingGemma-retrieval + lokaal GGUF-model (llama-cpp-python).

Runtime: llama-cpp-python in-process (CPU-wheels voor aarch64/amd64). Dat is
lichter dan een aparte llama.cpp-server: lazy load, idle-unload, geen extra
proces naast Home Assistant. Tests en CI gebruiken MockLLM.
"""

from __future__ import annotations

import gc
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol

from kippiesearch.config import Settings
from kippiesearch.logging_setup import get_logger
from kippiesearch.search import SearchService

log = get_logger("chat")

SYSTEM_PROMPT = (
    "Je bent KippieSearch, een lokale privacy-assistent. Je krijgt fragmenten "
    "uit bestanden op dit apparaat. Beantwoord de vraag alleen met die fragmenten.\n"
    "Regels:\n"
    "- Antwoord in dezelfde taal als de vraag.\n"
    "- Verwijs naar fragmenten als [1], [2], enzovoort.\n"
    "- Als de fragmenten het antwoord niet bevatten, zeg dat eerlijk. Verzin geen feiten.\n"
    "- Wees kort en duidelijk. Geen inleiding over wie je bent."
)

CHAT_PRESETS: dict[str, dict[str, Any]] = {
    "gemma3-1b": {
        "repo_id": "unsloth/gemma-3-1b-it-GGUF",
        "filename": "gemma-3-1b-it-Q4_K_M.gguf",
        "label": "Gemma 3 1B instruct Q4_K_M",
        "disk_mb": 806,
        "ram_mb": 1500,
        "tok_s_pi5": "15–30",
    },
    "gemma3-4b": {
        "repo_id": "unsloth/gemma-3-4b-it-GGUF",
        "filename": "gemma-3-4b-it-Q4_K_M.gguf",
        "label": "Gemma 3 4B instruct Q4_K_M",
        "disk_mb": 2490,
        "ram_mb": 3800,
        "tok_s_pi5": "5–12",
    },
}

_MOCK_NAMES = {"mock", "hash", "none", "off"}


def resolve_chat_spec(model: str) -> dict[str, Any]:
    """Preset-naam of Hugging Face 'repo:file' / 'repo/file.gguf'."""
    raw = (model or "gemma3-1b").strip()
    key = raw.lower()
    if key in CHAT_PRESETS:
        spec = dict(CHAT_PRESETS[key])
        spec["key"] = key
        return spec
    repo_id = raw
    filename = ""
    if ".gguf" in raw.lower():
        if ":" in raw:
            repo_id, filename = raw.split(":", 1)
        else:
            parts = raw.split("/")
            if len(parts) >= 3:
                repo_id = "/".join(parts[:2])
                filename = "/".join(parts[2:])
    elif ":" in raw:
        repo_id, filename = raw.split(":", 1)
    if not filename:
        filename = "gemma-3-1b-it-Q4_K_M.gguf"
        repo_id = raw.split(":")[0]
    return {
        "key": "custom",
        "repo_id": repo_id.strip(),
        "filename": filename.strip(),
        "label": f"{repo_id.strip()} / {filename.strip()}",
        "disk_mb": 0,
        "ram_mb": 0,
        "tok_s_pi5": "?",
    }


def llama_cpp_available() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except Exception:
        return False


def serialize_hit(hit: dict[str, Any], index: int) -> dict[str, Any]:
    path = str(hit.get("path") or "")
    name = path.rstrip("/").split("/")[-1] or path
    text = str(hit.get("text") or "")
    snippet = text.replace("\n", " ").strip()
    if len(snippet) > 280:
        snippet = snippet[:277] + "…"
    return {
        "n": index,
        "path": path,
        "filename": name,
        "title": hit.get("title") or name,
        "source": hit.get("source") or "",
        "file_type": hit.get("file_type") or "",
        "snippet": snippet,
        "score": hit.get("score") or 0,
        "mtime": hit.get("mtime"),
        "size": hit.get("size"),
        "open": f"api/file?path={_q(path)}",
        "download": f"api/file?path={_q(path)}&download=1",
    }


def _q(path: str) -> str:
    from urllib.parse import quote

    return quote(path, safe="")


def build_user_prompt(question: str, sources: list[dict[str, Any]]) -> str:
    if not sources:
        return (
            "Er zijn geen bronfragmenten gevonden.\n\n"
            f"Vraag: {question}\n\n"
            "Zeg eerlijk dat het antwoord niet in de geïndexeerde bestanden staat."
        )
    blocks = []
    for src in sources:
        blocks.append(
            f"[{src['n']}] {src['filename']} ({src['source'] or '?'})\n{src['snippet']}"
        )
    joined = "\n\n".join(blocks)
    return f"Fragmenten:\n{joined}\n\nVraag: {question}"


class ChatBackend(Protocol):
    name: str

    def info(self) -> dict[str, Any]: ...

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]: ...

    def unload(self) -> None: ...


class MockLLM:
    """Deterministische streaming-LLM voor tests, CI en screenshots."""

    name = "mock"

    def info(self) -> dict[str, Any]:
        return {
            "backend": "mock",
            "model": "mock",
            "loaded": True,
            "label": "Mock LLM (tests)",
        }

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = self._answer(user)
        for piece in _chunk_text(text):
            yield piece

    def unload(self) -> None:
        return

    def _answer(self, user: str) -> str:
        if "geen bronfragmenten" in user.lower() or "Fragmenten:" not in user:
            return (
                "Dat staat niet in de geïndexeerde bestanden. "
                "Indexeer eerst je NAS of schoolmap, of stel de vraag anders."
            )
        blocks = re.findall(r"\[(\d+)\] ([^\n]+)\n([^\[]+)", user)
        rest = user.split("Vraag:", 1)[-1].strip()
        if blocks:
            n, header, snippet = blocks[0]
            snippet = " ".join(snippet.split())[:240]
            return (
                f"Op basis van de bestanden: {snippet} [{n}]\n\n"
                f"Bron: {header}. Andere fragmenten staan in de kaarten hieronder."
            )
        return f"Ik heb fragmenten gevonden over: {rest[:80]} [1]"


class LlamaCppBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.spec = resolve_chat_spec(settings.chat_model)
        self.name = f"llama-cpp-python:{self.spec['key']}"
        self._llm = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._loaded_path = ""

    def info(self) -> dict[str, Any]:
        with self._lock:
            loaded = self._llm is not None
        return {
            "backend": "llama-cpp-python",
            "model": self.settings.chat_model,
            "preset": self.spec["key"],
            "label": self.spec["label"],
            "repo_id": self.spec["repo_id"],
            "filename": self.spec["filename"],
            "loaded": loaded,
            "path": self._loaded_path,
            "disk_mb": self.spec.get("disk_mb") or 0,
            "ram_mb_hint": self.spec.get("ram_mb") or 0,
            "tok_s_pi5": self.spec.get("tok_s_pi5") or "?",
        }

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        llm = self.ensure_loaded()
        try:
            stream = llm.create_chat_completion(
                messages=messages,
                temperature=float(self.settings.chat_temperature),
                max_tokens=int(self.settings.chat_max_tokens),
                stream=True,
            )
            for chunk in stream:
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content") or ""
                if text:
                    yield text
        except Exception as exc:  # noqa: BLE001
            log.warning("chat_completion_fallback", error=str(exc))
            prompt = _gemma_prompt(messages)
            out = llm.create_completion(
                prompt,
                temperature=float(self.settings.chat_temperature),
                max_tokens=int(self.settings.chat_max_tokens),
                stream=True,
            )
            for chunk in out:
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                text = (choices[0].get("text") or "")
                if text:
                    yield text
        finally:
            self._schedule_unload()

    def ensure_loaded(self):
        with self._lock:
            if self._llm is not None:
                return self._llm
            path = self._download()
            from llama_cpp import Llama

            log.info("chat_model_loading", path=str(path), n_ctx=self.settings.chat_n_ctx)
            self._llm = Llama(
                model_path=str(path),
                n_ctx=int(self.settings.chat_n_ctx),
                n_threads=int(self.settings.chat_n_threads),
                n_batch=min(256, int(self.settings.chat_n_ctx)),
                n_gpu_layers=0,
                logits_all=False,
                embedding=False,
                verbose=False,
            )
            self._loaded_path = str(path)
            log.info("chat_model_loaded", path=str(path))
            return self._llm

    def unload(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._llm is None:
                return
            try:
                del self._llm
            except Exception:
                pass
            self._llm = None
            self._loaded_path = ""
            gc.collect()
            log.info("chat_model_unloaded")

    def _download(self) -> Path:
        from huggingface_hub import hf_hub_download

        cache = self.settings.model_dir / "hf"
        cache.mkdir(parents=True, exist_ok=True)
        kwargs = {
            "repo_id": self.spec["repo_id"],
            "filename": self.spec["filename"],
            "cache_dir": str(cache),
        }
        try:
            path = hf_hub_download(**kwargs, local_files_only=True)
        except Exception:
            log.info(
                "chat_model_download",
                repo=self.spec["repo_id"],
                filename=self.spec["filename"],
            )
            path = hf_hub_download(**kwargs, local_files_only=False)
        return Path(path)

    def _schedule_unload(self) -> None:
        minutes = float(self.settings.chat_idle_unload_minutes or 0)
        if minutes <= 0:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(minutes * 60.0, self.unload)
            timer.daemon = True
            timer.start()
            self._timer = timer


class ChatEngine:
    def __init__(self, settings: Settings, searcher: SearchService) -> None:
        self.settings = settings
        self.searcher = searcher
        self.backend: ChatBackend = create_backend(settings)
        self._last_used = 0.0

    def info(self) -> dict[str, Any]:
        data = {
            "enabled": bool(self.settings.chat_enabled),
            "model": self.settings.chat_model,
            "n_ctx": self.settings.chat_n_ctx,
            "n_threads": self.settings.chat_n_threads,
            "max_tokens": self.settings.chat_max_tokens,
            "temperature": self.settings.chat_temperature,
            "idle_unload_minutes": self.settings.chat_idle_unload_minutes,
            "llama_cpp_available": llama_cpp_available(),
            "presets": {
                key: {
                    "label": spec["label"],
                    "disk_mb": spec["disk_mb"],
                    "ram_mb": spec["ram_mb"],
                    "tok_s_pi5": spec["tok_s_pi5"],
                }
                for key, spec in CHAT_PRESETS.items()
            },
        }
        data.update(self.backend.info())
        return data

    def retrieve(
        self,
        question: str,
        top_k: int | None = None,
        source: str | None = None,
        file_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        k = int(top_k or self.settings.chat_top_k or 6)
        hits = self.searcher.search(
            question,
            top_k=k,
            source=source,
            file_type=file_type,
            after=after,
            before=before,
        )
        return [serialize_hit(hit, i) for i, hit in enumerate(hits, start=1)]

    def events(
        self,
        question: str,
        top_k: int | None = None,
        source: str | None = None,
        file_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        question = (question or "").strip()
        if not question:
            yield "error", {"error": "lege vraag"}
            return
        if not self.settings.chat_enabled:
            yield "error", {"error": "Chat staat uit (optie chat_enabled)."}
            return
        sources = self.retrieve(
            question,
            top_k=top_k,
            source=source,
            file_type=file_type,
            after=after,
            before=before,
        )
        yield "sources", {"sources": sources}
        yield "status", {"phase": "generating", "message": "Antwoord genereren…"}
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(question, sources)},
        ]
        answer: list[str] = []
        try:
            for token in self.backend.stream(messages):
                if not token:
                    continue
                answer.append(token)
                yield "token", {"text": token}
            self._last_used = time.time()
            yield "done", {
                "answer": "".join(answer),
                "model": self.backend.name,
            }
        except Exception as exc:  # noqa: BLE001
            log.exception("chat_generate_failed")
            yield "error", {"error": str(exc)}

    def ask(
        self,
        question: str,
        top_k: int | None = None,
        source: str | None = None,
        file_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> dict[str, Any]:
        answer = ""
        sources: list[dict[str, Any]] = []
        error = ""
        for event, data in self.events(
            question,
            top_k=top_k,
            source=source,
            file_type=file_type,
            after=after,
            before=before,
        ):
            if event == "sources":
                sources = list(data.get("sources") or [])
            elif event == "token":
                answer += str(data.get("text") or "")
            elif event == "done":
                answer = str(data.get("answer") or answer)
            elif event == "error":
                error = str(data.get("error") or "onbekende fout")
        result: dict[str, Any] = {"answer": answer, "sources": sources}
        if error:
            result["error"] = error
        return result

    def unload(self) -> None:
        self.backend.unload()


def create_backend(settings: Settings) -> ChatBackend:
    model = (settings.chat_model or "gemma3-1b").strip().lower()
    if model in _MOCK_NAMES or not settings.chat_enabled:
        return MockLLM()
    if not llama_cpp_available():
        log.warning("llama_cpp_missing_using_mock")
        return MockLLM()
    return LlamaCppBackend(settings)


def _gemma_prompt(messages: list[dict[str, str]]) -> str:
    system = ""
    turns: list[dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "system" and not system:
            system = msg["content"].strip()
            continue
        turns.append(msg)
    parts = ["<bos>"]
    first = True
    for msg in turns:
        role = "model" if msg["role"] == "assistant" else "user"
        content = msg["content"].strip()
        if first and role == "user" and system:
            content = system + "\n\n" + content
            first = False
        parts.append(f"<start_of_turn>{role}\n{content}<end_of_turn>\n")
        first = False
    parts.append("<start_of_turn>model\n")
    return "".join(parts)


def _chunk_text(text: str) -> Iterator[str]:
    buf = ""
    for char in text:
        buf += char
        if char in " \n":
            yield buf
            buf = ""
    if buf:
        yield buf
