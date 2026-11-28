"""Cognee-style relational graph + RMM reflective utility memory over SQLite.

Extends :class:`~.experience_store.ExperienceStore` along two complementary axes
from the post-Reflexion agentic-memory literature without adding third-party
dependencies or relaxing AI-2027's evidence gate:

1. **Prospective relational structuring (Cognee / RMM prospective):** extracts
   canonical nodes (``capability``, ``provider``, ``artifact``, ``concept``) and
   typed edges from an admitted :class:`~.experience_store.ExperienceRecord` and
   indexes them idempotently by ``(output_fingerprint, node_id)`` so re-indexing
   the same verified artifact never duplicates nodes or inflates edge weights.
2. **Retrospective utility reranking (RMM, Tan et al. 2025):** records whether
   retrieved episodes later contributed to an independently verified downstream
   outcome (:meth:`ReflectiveGraphMemory.record_retrieval_outcome`) and re-ranks
   future searches with a Laplace-smoothed utility factor without deleting
   historical episodes.

Non-negotiable invariant
------------------------
**No verified result, no learning.** Every admission passes through
``ExperienceRecord.is_admissible`` (``verified is True``,
``test_result == "passed"``, SHA-256 ``output_fingerprint``, and
``cost_usd == 0.0``). Unverified episodes are refused via the underlying
:class:`~.experience_store.ExperienceStore` refusal log and never touch
``memory_nodes`` or ``memory_edges``.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field

from ._kernel_path import ensure_kernel_importable
from .celia_memory import cosine_similarity, lexical_embed_text
from .contracts import StrictModel
from .experience_store import ExperienceRecord, ExperienceStore

ensure_kernel_importable()
from kernel2027 import sha256_text  # noqa: E402

NodeKind = Literal["capability", "provider", "artifact", "concept", "failure_mode"]

_ARTIFACT_RE = re.compile(r"\b[a-zA-Z0-9_./-]+?\.(?:py|md|gs|cjs|js|ts|tsx|json|ya?ml|sql|toml)\b")
_CONCEPT_TOKEN_RE = re.compile(r"[a-z0-9_\u0600-\u06FF]{4,}")
_STOPWORDS = frozenset(
    {
        "with",
        "from",
        "that",
        "this",
        "into",
        "when",
        "then",
        "only",
        "over",
        "under",
        "after",
        "before",
        "have",
        "been",
        "were",
        "task",
        "step",
    }
)

_GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_nodes (
    node_id TEXT PRIMARY KEY, node_kind TEXT NOT NULL, canonical_key TEXT NOT NULL,
    label TEXT NOT NULL, fingerprint_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_nodes_kind ON memory_nodes(node_kind);
CREATE TABLE IF NOT EXISTS episode_node_links (
    output_fingerprint TEXT NOT NULL, episode_id TEXT NOT NULL, node_id TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (output_fingerprint, node_id)
);
CREATE INDEX IF NOT EXISTS idx_ep_links_episode ON episode_node_links(episode_id);
CREATE INDEX IF NOT EXISTS idx_ep_links_node ON episode_node_links(node_id);
CREATE TABLE IF NOT EXISTS memory_edges (
    source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,
    weight INTEGER NOT NULL DEFAULT 0, last_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_memory_edges_src ON memory_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_memory_edges_dst ON memory_edges(target_id);
CREATE TABLE IF NOT EXISTS edge_fingerprint_links (
    source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,
    output_fingerprint TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, relation, output_fingerprint)
);
CREATE TABLE IF NOT EXISTS retrieval_utility (
    episode_id TEXT PRIMARY KEY, retrieved_count INTEGER NOT NULL DEFAULT 0,
    verified_helpful_count INTEGER NOT NULL DEFAULT 0,
    unverified_or_failed_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
"""


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_key(raw: str) -> str:
    """Normalize ``raw`` into a compact, deterministic node key."""
    cleaned = " ".join(raw.strip().lower().split())
    return cleaned[:160]


class GraphNodeSpec(StrictModel):
    """Caller-supplied or extracted relational graph node."""

    node_kind: NodeKind
    key: str = Field(min_length=1, max_length=160)
    label: str = Field(default="", max_length=200)

    @property
    def canonical_key(self) -> str:
        """Return normalized lookup key."""
        return _canonical_key(self.key)

    @property
    def node_id(self) -> str:
        """Return globally unique ``kind:canonical_key`` identifier."""
        return f"{self.node_kind}:{self.canonical_key}"


class GraphEdgeSpec(StrictModel):
    """Directed typed edge between two :class:`GraphNodeSpec` identifiers."""

    source_id: str = Field(min_length=3, max_length=190)
    target_id: str = Field(min_length=3, max_length=190)
    relation: str = Field(min_length=1, max_length=64)


class GraphNodeRecord(StrictModel):
    """Persisted relational node returned by graph inspection queries."""

    node_id: str
    node_kind: str
    canonical_key: str
    label: str
    fingerprint_count: int = Field(ge=0)
    updated_at: str


class GraphEdgeRecord(StrictModel):
    """Persisted directed relation with deduplicated evidence weight."""

    source_id: str
    target_id: str
    relation: str
    weight: int = Field(ge=1)
    last_fingerprint: str
    updated_at: str


class RankedExperienceHit(StrictModel):
    """Experience retrieval hit enriched with graph overlap and RMM utility."""

    record: ExperienceRecord
    combined_score: float
    lexical_score: float
    graph_overlap: float = Field(ge=0.0, le=1.0)
    utility_score: float = Field(ge=0.0, le=1.0)
    retrieved_count: int = Field(ge=0)
    verified_helpful_count: int = Field(ge=0)


def extract_prospective_graph(
    record: ExperienceRecord,
    *,
    max_concepts: int = 8,
) -> tuple[tuple[GraphNodeSpec, ...], tuple[GraphEdgeSpec, ...]]:
    """Deterministically extract graph nodes and edges from ``record`` ($0 cost)."""
    cap_node = GraphNodeSpec(
        node_kind="capability",
        key=record.task_type,
        label=record.task_type,
    )
    prov_node = GraphNodeSpec(
        node_kind="provider",
        key=f"{record.provider}/{record.model}",
        label=f"{record.provider}/{record.model}",
    )
    nodes: dict[str, GraphNodeSpec] = {
        cap_node.node_id: cap_node,
        prov_node.node_id: prov_node,
    }
    edges: list[GraphEdgeSpec] = [
        GraphEdgeSpec(
            source_id=cap_node.node_id,
            target_id=prov_node.node_id,
            relation="served_by",
        )
    ]

    summary = record.task_summary or ""
    artifacts = sorted(set(_ARTIFACT_RE.findall(summary)))[:6]
    for path_token in artifacts:
        art_node = GraphNodeSpec(
            node_kind="artifact",
            key=path_token,
            label=path_token,
        )
        nodes[art_node.node_id] = art_node
        edges.append(
            GraphEdgeSpec(
                source_id=cap_node.node_id,
                target_id=art_node.node_id,
                relation="touches_artifact",
            )
        )

    lowered = summary.lower()
    seen_concepts: list[GraphNodeSpec] = []
    for match in _CONCEPT_TOKEN_RE.finditer(lowered):
        tok = match.group(0)
        if tok in _STOPWORDS or tok.isdigit():
            continue
        concept_node = GraphNodeSpec(node_kind="concept", key=tok, label=tok)
        if concept_node.node_id in nodes:
            continue
        nodes[concept_node.node_id] = concept_node
        seen_concepts.append(concept_node)
        edges.append(
            GraphEdgeSpec(
                source_id=cap_node.node_id,
                target_id=concept_node.node_id,
                relation="involves_concept",
            )
        )
        if len(seen_concepts) >= max_concepts:
            break

    for idx in range(len(seen_concepts) - 1):
        edges.append(
            GraphEdgeSpec(
                source_id=seen_concepts[idx].node_id,
                target_id=seen_concepts[idx + 1].node_id,
                relation="co_occurs_with",
            )
        )
    return tuple(nodes.values()), tuple(edges)


def compute_utility_score(retrieved_count: int, verified_helpful_count: int) -> float:
    """Return Laplace-smoothed Beta(1, 1) posterior utility in ``(0, 1)``."""
    safe_total = max(0, retrieved_count)
    safe_helpful = max(0, min(verified_helpful_count, safe_total))
    return (safe_helpful + 1.0) / (safe_total + 2.0)


class ReflectiveGraphMemory:
    """SQLite relational graph + RMM retrospective utility layer over ``ExperienceStore``."""

    def __init__(self, db_path: Path | str) -> None:
        """Open ``ExperienceStore`` and initialize relational/utility tables on ``db_path``."""
        self.db_path = Path(db_path)
        self.experience_store = ExperienceStore(self.db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_GRAPH_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close both the graph connection and the underlying ``ExperienceStore``."""
        self._conn.close()
        self.experience_store.close()

    def __enter__(self) -> ReflectiveGraphMemory:
        """Enter context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close SQLite handles on exit."""
        self.close()

    def index_episode(
        self,
        record: ExperienceRecord,
        *,
        extra_nodes: Sequence[GraphNodeSpec] = (),
        extra_edges: Sequence[GraphEdgeSpec] = (),
    ) -> bool:
        """Admit ``record`` if verified and index its graph nodes/edges idempotently.

        Returns ``True`` when ``record`` satisfies the admissibility gate (either
        newly admitted or re-indexed by matching ``output_fingerprint``), and
        ``False`` when refused by the evidence gate.
        """
        with self._lock:
            existing = self.experience_store.get(record.episode_id)
            if existing is None:
                admitted = self.experience_store.record(record)
                if not admitted:
                    return False
            elif (
                not record.is_admissible or existing.output_fingerprint != record.output_fingerprint
            ):
                return False

            auto_nodes, auto_edges = extract_prospective_graph(record)
            all_nodes: dict[str, GraphNodeSpec] = {n.node_id: n for n in auto_nodes}
            for node in extra_nodes:
                all_nodes[node.node_id] = node

            now = _utc_now()
            fp = record.output_fingerprint
            for node in all_nodes.values():
                self._upsert_node_for_fingerprint(
                    node=node,
                    episode_id=record.episode_id,
                    output_fingerprint=fp,
                    now=now,
                )

            combined_edges = list(auto_edges) + list(extra_edges)
            for edge in combined_edges:
                if edge.source_id in all_nodes and edge.target_id in all_nodes:
                    self._upsert_edge_for_fingerprint(
                        edge=edge,
                        output_fingerprint=fp,
                        now=now,
                    )
            self._conn.commit()
            return True

    def _upsert_node_for_fingerprint(
        self,
        *,
        node: GraphNodeSpec,
        episode_id: str,
        output_fingerprint: str,
        now: str,
    ) -> None:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO episode_node_links "
            "(output_fingerprint, episode_id, node_id, created_at) VALUES (?,?,?,?)",
            (output_fingerprint, episode_id, node.node_id, now),
        )
        delta = 1 if cur.rowcount == 1 else 0
        label = node.label or node.canonical_key
        self._conn.execute(
            """
            INSERT INTO memory_nodes (
                node_id, node_kind, canonical_key, label, fingerprint_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                label = excluded.label,
                fingerprint_count = memory_nodes.fingerprint_count + ?,
                updated_at = excluded.updated_at
            """,
            (node.node_id, node.node_kind, node.canonical_key, label, delta, now, delta),
        )

    def _upsert_edge_for_fingerprint(
        self,
        *,
        edge: GraphEdgeSpec,
        output_fingerprint: str,
        now: str,
    ) -> None:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO edge_fingerprint_links "
            "(source_id, target_id, relation, output_fingerprint) VALUES (?,?,?,?)",
            (edge.source_id, edge.target_id, edge.relation, output_fingerprint),
        )
        if cur.rowcount == 0:
            return
        self._conn.execute(
            """
            INSERT INTO memory_edges (
                source_id, target_id, relation, weight, last_fingerprint, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                weight = memory_edges.weight + 1,
                last_fingerprint = excluded.last_fingerprint,
                updated_at = excluded.updated_at
            """,
            (edge.source_id, edge.target_id, edge.relation, output_fingerprint, now),
        )

    def record_retrieval_outcome(
        self,
        episode_ids: Sequence[str],
        *,
        downstream_verified: bool,
    ) -> int:
        """Update RMM retrospective utility counters for retrieved ``episode_ids``."""
        now = _utc_now()
        updated = 0
        helpful_inc = 1 if downstream_verified else 0
        failed_inc = 0 if downstream_verified else 1
        with self._lock:
            for episode_id in dict.fromkeys(episode_ids):
                if self.experience_store.get(episode_id) is None:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO retrieval_utility (
                        episode_id, retrieved_count, verified_helpful_count,
                        unverified_or_failed_count, updated_at
                    ) VALUES (?, 1, ?, ?, ?)
                    ON CONFLICT(episode_id) DO UPDATE SET
                        retrieved_count = retrieval_utility.retrieved_count + 1,
                        verified_helpful_count = retrieval_utility.verified_helpful_count + ?,
                        unverified_or_failed_count = (
                            retrieval_utility.unverified_or_failed_count + ?
                        ),
                        updated_at = excluded.updated_at
                    """,
                    (episode_id, helpful_inc, failed_inc, now, helpful_inc, failed_inc),
                )
                updated += 1
            self._conn.commit()
        return updated

    def utility_stats(self, episode_id: str) -> tuple[int, int, float]:
        """Return ``(retrieved_count, verified_helpful_count, smoothed_utility)``."""
        row = self._conn.execute(
            "SELECT retrieved_count, verified_helpful_count FROM retrieval_utility "
            "WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
        if row is None:
            return 0, 0, compute_utility_score(0, 0)
        retrieved = int(row["retrieved_count"])
        helpful = int(row["verified_helpful_count"])
        return retrieved, helpful, compute_utility_score(retrieved, helpful)

    def _query_seed_nodes(self, query: str) -> set[str]:
        seeds: set[str] = set()
        for path_token in _ARTIFACT_RE.findall(query):
            seeds.add(f"artifact:{_canonical_key(path_token)}")
        for match in _CONCEPT_TOKEN_RE.finditer(query.lower()):
            tok = match.group(0)
            if tok not in _STOPWORDS and not tok.isdigit():
                seeds.add(f"concept:{_canonical_key(tok)}")
        if not seeds:
            return set()
        expanded = set(seeds)
        placeholders = ",".join("?" for _ in seeds)
        seed_tuple = tuple(seeds)
        rows = self._conn.execute(
            f"SELECT source_id, target_id FROM memory_edges "  # noqa: S608
            f"WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            seed_tuple + seed_tuple,
        ).fetchall()
        for row in rows:
            expanded.add(str(row["source_id"]))
            expanded.add(str(row["target_id"]))
        return expanded

    def _fingerprint_graph_overlap(
        self,
        output_fingerprint: str,
        seed_neighborhood: set[str],
    ) -> float:
        if not seed_neighborhood:
            return 0.0
        rows = self._conn.execute(
            "SELECT node_id FROM episode_node_links WHERE output_fingerprint = ?",
            (output_fingerprint,),
        ).fetchall()
        ep_nodes = {str(row["node_id"]) for row in rows}
        if not ep_nodes:
            return 0.0
        matched = len(ep_nodes & seed_neighborhood)
        denom = max(1, min(len(ep_nodes), len(seed_neighborhood)))
        return min(1.0, matched / denom)

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        task_type: str | None = None,
        rerank_by_utility: bool = True,
        graph_boost: float = 0.20,
        utility_weight: float = 0.30,
    ) -> list[RankedExperienceHit]:
        """Rank verified experiences using lexical cosine + graph overlap + RMM utility."""
        candidates = self.experience_store.recent(
            limit=max(k * 20, 200),
            task_type=task_type,
        )
        if not candidates:
            return []
        query_vec = lexical_embed_text(query)
        seed_neighborhood = self._query_seed_nodes(query)
        hits: list[RankedExperienceHit] = []
        base_weight = max(0.0, 1.0 - graph_boost - (utility_weight if rerank_by_utility else 0.0))
        for record in candidates:
            lex_score = cosine_similarity(query_vec, lexical_embed_text(record.task_summary))
            overlap = self._fingerprint_graph_overlap(record.output_fingerprint, seed_neighborhood)
            retrieved, helpful, utility = self.utility_stats(record.episode_id)
            combined = base_weight * lex_score + graph_boost * overlap
            if rerank_by_utility:
                combined += utility_weight * utility
            if record.is_rejected:
                combined *= 0.25
            hits.append(
                RankedExperienceHit(
                    record=record,
                    combined_score=round(combined, 6),
                    lexical_score=round(lex_score, 6),
                    graph_overlap=round(overlap, 6),
                    utility_score=round(utility, 6),
                    retrieved_count=retrieved,
                    verified_helpful_count=helpful,
                )
            )
        hits.sort(
            key=lambda item: (item.combined_score, item.utility_score, item.lexical_score),
            reverse=True,
        )
        return hits[:k]

    def get_node(self, node_id: str) -> GraphNodeRecord | None:
        """Return persisted :class:`GraphNodeRecord` for ``node_id``, if present."""
        row = self._conn.execute(
            "SELECT * FROM memory_nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return GraphNodeRecord(
            node_id=str(row["node_id"]),
            node_kind=str(row["node_kind"]),
            canonical_key=str(row["canonical_key"]),
            label=str(row["label"]),
            fingerprint_count=int(row["fingerprint_count"]),
            updated_at=str(row["updated_at"]),
        )

    def neighbors(self, node_id: str, *, limit: int = 20) -> list[GraphEdgeRecord]:
        """Return highest-weight incident edges for ``node_id``."""
        rows = self._conn.execute(
            "SELECT * FROM memory_edges WHERE source_id = ? OR target_id = ? "
            "ORDER BY weight DESC, relation ASC LIMIT ?",
            (node_id, node_id, limit),
        ).fetchall()
        return [
            GraphEdgeRecord(
                source_id=str(row["source_id"]),
                target_id=str(row["target_id"]),
                relation=str(row["relation"]),
                weight=int(row["weight"]),
                last_fingerprint=str(row["last_fingerprint"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def graph_digest(self) -> str:
        """Return a canonical SHA-256 digest of all graph nodes and edges."""
        node_rows = self._conn.execute(
            "SELECT node_id, fingerprint_count FROM memory_nodes ORDER BY node_id ASC"
        ).fetchall()
        edge_rows = self._conn.execute(
            "SELECT source_id, target_id, relation, weight FROM memory_edges "
            "ORDER BY source_id ASC, target_id ASC, relation ASC"
        ).fetchall()
        lines = [f"N|{r['node_id']}|{r['fingerprint_count']}" for r in node_rows]
        lines.extend(
            f"E|{r['source_id']}|{r['relation']}|{r['target_id']}|{r['weight']}" for r in edge_rows
        )
        return sha256_text("\n".join(lines))

    def stats(self) -> dict[str, int]:
        """Return counts across experience, graph, and utility tables."""
        base = self.experience_store.stats()
        nodes = int(self._conn.execute("SELECT COUNT(*) AS c FROM memory_nodes").fetchone()["c"])
        edges = int(self._conn.execute("SELECT COUNT(*) AS c FROM memory_edges").fetchone()["c"])
        tracked = int(
            self._conn.execute("SELECT COUNT(*) AS c FROM retrieval_utility").fetchone()["c"]
        )
        return {
            **base,
            "nodes": nodes,
            "edges": edges,
            "utility_tracked": tracked,
        }

    def verify_durability(self) -> list[str]:
        """Re-open SQLite from disk and check cross-table referential integrity."""
        if not self.db_path.is_file():
            return [f"database file missing on disk: {self.db_path}"]
        problems = list(self.experience_store.verify_durability())
        reopened = sqlite3.connect(str(self.db_path), timeout=15.0)
        reopened.row_factory = sqlite3.Row
        try:
            orphan_links = reopened.execute(
                "SELECT episode_id, node_id FROM episode_node_links "
                "WHERE episode_id NOT IN (SELECT episode_id FROM experiences)"
            ).fetchall()
            for row in orphan_links:
                problems.append(
                    f"orphan episode_node_link: {row['episode_id']} -> {row['node_id']}"
                )
            orphan_nodes = reopened.execute(
                "SELECT source_id, target_id FROM memory_edges "
                "WHERE source_id NOT IN (SELECT node_id FROM memory_nodes) "
                "OR target_id NOT IN (SELECT node_id FROM memory_nodes)"
            ).fetchall()
            for row in orphan_nodes:
                problems.append(f"dangling memory_edge: {row['source_id']} -> {row['target_id']}")
        finally:
            reopened.close()
        return problems


__all__ = [
    "GraphEdgeRecord",
    "GraphEdgeSpec",
    "GraphNodeRecord",
    "GraphNodeSpec",
    "NodeKind",
    "RankedExperienceHit",
    "ReflectiveGraphMemory",
    "compute_utility_score",
    "extract_prospective_graph",
]
