"""Celia Memory Intelligence: persistent SQLite RAG over workspace docs and notes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from .ai_providers import ProviderError
from .contracts import StrictModel
from .ollama_layer import OllamaRuntimeClient
from .security import SecurityValidationError, resolve_safe_path

_ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TOKEN_RE = re.compile(r"[a-z0-9\u0600-\u06FF]+")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_SKIP_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".ai2027_studio",
        ".ai2027_work",
    }
)
_FORBIDDEN_FILES = frozenset({".env", ".env.local", "env.local", "local-pairings.json"})
_LEXICAL_DIM = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id        TEXT PRIMARY KEY,
    source_path     TEXT NOT NULL,
    heading         TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    vector_json     TEXT NOT NULL,
    vector_dim      INTEGER NOT NULL,
    retrieval_mode  TEXT NOT NULL,
    embed_model     TEXT NOT NULL,
    is_neural       INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rag_path ON rag_chunks(source_path);
CREATE INDEX IF NOT EXISTS idx_rag_sha  ON rag_chunks(content_sha256);
CREATE TABLE IF NOT EXISTS rag_index_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    files_indexed   INTEGER NOT NULL,
    chunks_written  INTEGER NOT NULL,
    chunks_skipped  INTEGER NOT NULL,
    retrieval_mode  TEXT NOT NULL,
    embed_model     TEXT NOT NULL
);
"""


class TextChunk(StrictModel):
    """One deterministic chunk sliced from a repository Markdown or text document."""

    chunk_id: str
    source_path: str
    heading: str
    chunk_index: int = Field(ge=0)
    content: str
    content_sha256: str


class CeliaMemoryHit(StrictModel):
    """Ranked semantic or lexical retrieval result with provenance."""

    chunk_id: str
    source_path: str
    heading: str
    snippet: str
    score: float
    content_sha256: str
    retrieval_mode: Literal["neural_ollama", "lexical_fallback"]
    embed_model: str
    is_neural: bool


class CeliaIndexSummary(StrictModel):
    """Measured report produced after indexing files into Celia Memory SQLite store."""

    db_path: str
    files_scanned: int = Field(ge=0)
    chunks_written: int = Field(ge=0)
    chunks_reused: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    retrieval_mode: Literal["neural_ollama", "lexical_fallback"]
    embed_model: str
    is_neural: bool


class CeliaMemoryStats(StrictModel):
    """Persistent store summary confirming cross-restart durability."""

    db_path: str
    total_chunks: int = Field(ge=0)
    unique_files: int = Field(ge=0)
    neural_chunks: int = Field(ge=0)
    lexical_chunks: int = Field(ge=0)
    index_runs: int = Field(ge=0)


@runtime_checkable
class EmbeddingBackendProtocol(Protocol):
    """Replaceable contract for embedding batches of document or query strings."""

    @property
    def backend_name(self) -> str:
        """Return descriptive backend or model identifier."""
        ...

    @property
    def is_neural(self) -> bool:
        """Return True only when embeddings come from a real neural model."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode texts into L2-normalized dense vectors."""
        ...


def normalize_bilingual_text(text: str) -> str:
    """Normalize Arabic diacritics/alef/ya/ta-marbuta and English case for matching."""
    t = unicodedata.normalize("NFKC", text).translate(_PERSIAN_DIGITS).lower()
    t = _ARABIC_DIACRITICS.sub("", t)
    t = t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"\s+", " ", t).strip()


def _lexical_tokens(text: str) -> list[str]:
    norm = normalize_bilingual_text(text)
    uni = _TOKEN_RE.findall(norm)
    bi = [f"{a}_{b}" for a, b in zip(uni, uni[1:], strict=False)]
    return uni + bi


def lexical_embed_text(text: str, *, dim: int = _LEXICAL_DIM) -> list[float]:
    """Deterministic stdlib feature-hashed L2 vector for honest offline fallback."""
    vec = [0.0] * dim
    for tok in _lexical_tokens(text):
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [round(v / norm, 8) for v in vec]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute bounded cosine similarity for equal-length vectors."""
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


class DeterministicLexicalEmbeddingBackend:
    """Offline stdlib embedding fallback explicitly labeled non-neural."""

    backend_name = "deterministic-lexical-v1"
    is_neural = False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Hash normalized English/Arabic n-grams into deterministic vectors."""
        await asyncio.sleep(0)
        return [lexical_embed_text(t) for t in texts]


class OllamaEmbeddingBackend:
    """Neural embedding backend powered by Ollama /api/embed (e.g. embeddinggemma)."""

    def __init__(
        self,
        client: OllamaRuntimeClient,
        *,
        model: str | None = None,
        unload_after: bool | None = None,
    ) -> None:
        """Bind an OllamaRuntimeClient and model for local RAG embeddings."""
        self._client = client
        self._model = (model or client.profile.embed_model).strip()
        self._unload_after = (
            client.profile.unload_after_embed if unload_after is None else unload_after
        )

    @property
    def backend_name(self) -> str:
        """Return configured Ollama embedding model name."""
        return self._model

    @property
    def is_neural(self) -> bool:
        """Return True because vectors originate from an Ollama embedding model."""
        return True

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Invoke Ollama /api/embed respecting the 4GB single-model unload policy."""
        return await self._client.embed(
            texts,
            model=self._model,
            keep_alive=0 if self._unload_after else None,
        )


def chunk_document(
    rel_path: str,
    text: str,
    *,
    max_chars: int = 720,
    overlap_chars: int = 80,
) -> list[TextChunk]:
    """Split a Markdown or text file into heading-aware deterministic chunks."""
    if not text.strip():
        return []
    sections: list[tuple[str, str]] = []
    current_heading = "Document Root"
    buf: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if buf:
                sections.append((current_heading, "\n".join(buf).strip()))
                buf.clear()
            current_heading = m.group(2).strip()[:120]
        else:
            buf.append(line)
    if buf:
        sections.append((current_heading, "\n".join(buf).strip()))

    chunks: list[TextChunk] = []
    idx = 0
    step = max(120, max_chars - max(0, min(overlap_chars, max_chars // 3)))
    for heading, body in sections:
        cleaned = body.strip()
        if not cleaned:
            continue
        pos = 0
        while pos < len(cleaned):
            piece = cleaned[pos : pos + max_chars].strip()
            if piece:
                digest = hashlib.sha256(f"{rel_path}\n{heading}\n{piece}".encode()).hexdigest()
                chunks.append(
                    TextChunk(
                        chunk_id=digest[:24],
                        source_path=rel_path,
                        heading=heading,
                        chunk_index=idx,
                        content=piece,
                        content_sha256=digest,
                    )
                )
                idx += 1
            if pos + max_chars >= len(cleaned):
                break
            pos += step
    return chunks


class CeliaMemoryStore:
    """Persistent SQLite semantic/lexical RAG store for AI-2027 and CeliaOS docs."""

    def __init__(
        self,
        db_path: Path,
        *,
        backend: EmbeddingBackendProtocol | None = None,
        allow_lexical_fallback: bool = True,
    ) -> None:
        """Create or open a persistent SQLite RAG database with replaceable embedder."""
        self.db_path = db_path.resolve()
        self._backend: EmbeddingBackendProtocol = backend or DeterministicLexicalEmbeddingBackend()
        self._allow_lexical_fallback = allow_lexical_fallback
        self._fallback = DeterministicLexicalEmbeddingBackend()
        self._init_db_sync()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _collect_chunks_sync(
        self, workspace_root: Path, rel_paths: Sequence[str], max_files: int
    ) -> tuple[list[TextChunk], int]:
        root = workspace_root.resolve()
        files: list[Path] = []
        for rel in rel_paths:
            target = resolve_safe_path(root, rel)
            if target.is_file():
                files.append(target)
            elif target.is_dir():
                for candidate in sorted(target.rglob("*")):
                    if not candidate.is_file():
                        continue
                    rel_parts = candidate.relative_to(root).parts
                    if any(part in _SKIP_PARTS for part in rel_parts):
                        continue
                    if candidate.name in _FORBIDDEN_FILES:
                        continue
                    if candidate.suffix.lower() not in {".md", ".txt", ".rst", ".py", ".json"}:
                        continue
                    files.append(candidate)
                    if len(files) >= max_files:
                        break
            if len(files) >= max_files:
                break

        all_chunks: list[TextChunk] = []
        scanned = 0
        for file_path in files[:max_files]:
            if file_path.name in _FORBIDDEN_FILES:
                continue
            try:
                raw = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            rel_str = file_path.relative_to(root).as_posix()
            all_chunks.extend(chunk_document(rel_str, raw))
        return all_chunks, scanned

    async def index_paths(
        self,
        workspace_root: Path,
        rel_paths: Sequence[str],
        *,
        max_files: int = 80,
    ) -> CeliaIndexSummary:
        """Index validated workspace documents into persistent SQLite RAG storage."""
        chunks, files_scanned = await asyncio.to_thread(
            self._collect_chunks_sync, workspace_root, rel_paths, max_files
        )
        existing_hashes = await asyncio.to_thread(self._existing_hashes_sync)
        to_embed = [c for c in chunks if c.content_sha256 not in existing_hashes]
        reused = len(chunks) - len(to_embed)

        active_backend = self._backend
        if to_embed:
            try:
                vectors = await active_backend.embed(
                    [f"{c.heading}\n{c.content}" for c in to_embed]
                )
            except (ProviderError, ValueError):
                if not self._allow_lexical_fallback:
                    raise
                active_backend = self._fallback
                vectors = await active_backend.embed(
                    [f"{c.heading}\n{c.content}" for c in to_embed]
                )
        else:
            vectors = []

        mode: Literal["neural_ollama", "lexical_fallback"] = (
            "neural_ollama" if active_backend.is_neural else "lexical_fallback"
        )
        total = await asyncio.to_thread(
            self._persist_chunks_sync,
            to_embed,
            vectors,
            files_scanned,
            reused,
            mode,
            active_backend.backend_name,
            active_backend.is_neural,
        )
        return CeliaIndexSummary(
            db_path=str(self.db_path),
            files_scanned=files_scanned,
            chunks_written=len(to_embed),
            chunks_reused=reused,
            total_chunks=total,
            retrieval_mode=mode,
            embed_model=active_backend.backend_name,
            is_neural=active_backend.is_neural,
        )

    def _existing_hashes_sync(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT content_sha256 FROM rag_chunks").fetchall()
        return {str(r["content_sha256"]) for r in rows}

    def _persist_chunks_sync(
        self,
        chunks: Sequence[TextChunk],
        vectors: Sequence[Sequence[float]],
        files_scanned: int,
        reused: int,
        mode: str,
        embed_model: str,
        is_neural: bool,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            for chunk, vec in zip(chunks, vectors, strict=True):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO rag_chunks (
                        chunk_id, source_path, heading, chunk_index, content,
                        content_sha256, vector_json, vector_dim, retrieval_mode,
                        embed_model, is_neural, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.source_path,
                        chunk.heading,
                        chunk.chunk_index,
                        chunk.content,
                        chunk.content_sha256,
                        json.dumps(list(vec)),
                        len(vec),
                        mode,
                        embed_model,
                        1 if is_neural else 0,
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO rag_index_runs (
                    created_at, files_indexed, chunks_written, chunks_skipped,
                    retrieval_mode, embed_model
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now, files_scanned, len(chunks), reused, mode, embed_model),
            )
            total_row = conn.execute("SELECT COUNT(*) AS c FROM rag_chunks").fetchone()
            conn.commit()
        return int(total_row["c"]) if total_row else 0

    async def search(self, query: str, *, k: int = 5) -> list[CeliaMemoryHit]:
        """Retrieve top-k verified chunks matching query semantically or lexically."""
        cleaned = query.strip()
        if not cleaned:
            raise SecurityValidationError("Memory RAG search query must not be blank.")
        limit = max(1, min(k, 20))
        active_backend = self._backend
        try:
            q_vec = (await active_backend.embed([cleaned]))[0]
        except (ProviderError, ValueError):
            if not self._allow_lexical_fallback:
                raise
            active_backend = self._fallback
            q_vec = (await active_backend.embed([cleaned]))[0]
        lexical_q_vec = lexical_embed_text(cleaned)
        q_tokens = set(_lexical_tokens(cleaned))
        return await asyncio.to_thread(
            self._search_sync,
            q_vec,
            lexical_q_vec,
            q_tokens,
            active_backend.is_neural,
            limit,
        )

    def _search_sync(
        self,
        q_vec: Sequence[float],
        lexical_q_vec: Sequence[float],
        q_tokens: set[str],
        query_is_neural: bool,
        limit: int,
    ) -> list[CeliaMemoryHit]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM rag_chunks").fetchall()
        scored: list[CeliaMemoryHit] = []
        for row in rows:
            vec = json.loads(str(row["vector_json"]))
            chunk_is_neural = bool(row["is_neural"])
            content = str(row["content"])
            heading = str(row["heading"])
            if query_is_neural and chunk_is_neural and len(vec) == len(q_vec):
                sim = cosine_similarity(q_vec, vec)
                used_mode: Literal["neural_ollama", "lexical_fallback"] = "neural_ollama"
                is_neural = True
            elif not chunk_is_neural and len(vec) == len(lexical_q_vec):
                sim = cosine_similarity(lexical_q_vec, vec)
                used_mode = "lexical_fallback"
                is_neural = False
            else:
                # Dimension/mode mismatch (e.g. querying neural-indexed store offline)
                chunk_lex = lexical_embed_text(f"{heading}\n{content}")
                sim = cosine_similarity(lexical_q_vec, chunk_lex)
                used_mode = "lexical_fallback"
                is_neural = False

            doc_tokens = set(_lexical_tokens(f"{heading} {content}"))
            overlap = (len(q_tokens & doc_tokens) / len(q_tokens)) if q_tokens else 0.0
            final_score = round(0.75 * max(0.0, sim) + 0.25 * overlap, 4)
            if final_score <= 0.0:
                continue
            snippet = " ".join(content.split())[:240]
            scored.append(
                CeliaMemoryHit(
                    chunk_id=str(row["chunk_id"]),
                    source_path=str(row["source_path"]),
                    heading=heading,
                    snippet=snippet,
                    score=final_score,
                    content_sha256=str(row["content_sha256"]),
                    retrieval_mode=used_mode,
                    embed_model=str(row["embed_model"]),
                    is_neural=is_neural,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    async def stats(self) -> CeliaMemoryStats:
        """Return persisted chunk, file, and backend counts from SQLite."""
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> CeliaMemoryStats:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0])
            unique_files = int(
                conn.execute("SELECT COUNT(DISTINCT source_path) FROM rag_chunks").fetchone()[0]
            )
            neural = int(
                conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE is_neural = 1").fetchone()[0]
            )
            lexical = int(
                conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE is_neural = 0").fetchone()[0]
            )
            runs = int(conn.execute("SELECT COUNT(*) FROM rag_index_runs").fetchone()[0])
        return CeliaMemoryStats(
            db_path=str(self.db_path),
            total_chunks=total,
            unique_files=unique_files,
            neural_chunks=neural,
            lexical_chunks=lexical,
            index_runs=runs,
        )

    @staticmethod
    def default_db_path(workspace_root: Path) -> Path:
        """Return default persistent workspace SQLite path outside git commits."""
        raw = os.environ.get("AI2027_CELIA_MEMORY_DB", "").strip()
        if raw:
            return Path(raw).resolve()
        return (workspace_root / ".ai2027_studio" / "celia_memory.sqlite3").resolve()
