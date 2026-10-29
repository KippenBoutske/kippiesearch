"""Chunking met overlap. Metadata blijft per chunk beschikbaar."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    text: str
    index: int
    start: int
    end: int


def chunk_text(
    text: str,
    chunk_chars: int = 1800,
    overlap: int = 220,
) -> list[Chunk]:
    cleaned = (text or "").replace("\x00", " ").strip()
    if not cleaned:
        return []
    if chunk_chars <= 0:
        return [Chunk(text=cleaned, index=0, start=0, end=len(cleaned))]
    overlap = max(0, min(overlap, chunk_chars // 2))
    chunks: list[Chunk] = []
    start = 0
    index = 0
    length = len(cleaned)
    while start < length:
        end = min(length, start + chunk_chars)
        if end < length:
            break_at = _prefer_break(cleaned, start, end)
            end = break_at
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(Chunk(text=piece, index=index, start=start, end=end))
            index += 1
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _prefer_break(text: str, start: int, end: int) -> int:
    window = text[start:end]
    for sep in ("\n\n", "\n", ". ", " "):
        pos = window.rfind(sep)
        if pos >= max(80, len(window) // 4):
            return start + pos + len(sep)
    return end
