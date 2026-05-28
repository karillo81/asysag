"""Embedded ChromaDB knowledge base.

Ingests markdown files from configured directories (docs + runbooks) into a
local persistent ChromaDB collection. The default embedding function uses
all-MiniLM-L6-v2 via ONNX, runs in-process, no network calls after the
one-time model download on first init.

Chunking strategy: split on blank lines (paragraph-level). Each chunk keeps
the source path and the chunk index in metadata. Chunk IDs are stable
({source_relpath}#{index}), so re-ingest is idempotent — modified files
produce updated chunks for the same IDs.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import chromadb

logger = logging.getLogger(__name__)


def _chunks_for_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    raw_chunks = re.split(r"\n\s*\n", text)
    cleaned = [c.strip() for c in raw_chunks if c.strip()]
    return cleaned


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class KnowledgeBase:
    def __init__(
        self,
        persist_dir: Path,
        source_dirs: list[Path],
        collection_name: str = "autosys_docs",
    ):
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_or_create_collection(collection_name)
        self._source_dirs = source_dirs

    def ingest(self) -> dict[str, int]:
        """Walk source dirs, upsert all markdown chunks. Returns counts per source."""
        counts: dict[str, int] = {}
        for src_dir in self._source_dirs:
            if not src_dir.exists():
                continue
            for md_path in sorted(src_dir.rglob("*.md")):
                rel = md_path.relative_to(src_dir.parent).as_posix()
                chunks = _chunks_for_file(md_path)
                if not chunks:
                    continue
                ids = [f"{rel}#{i}" for i in range(len(chunks))]
                metadatas = [
                    {
                        "source": rel,
                        "chunk_index": i,
                        "content_hash": _hash(chunk),
                    }
                    for i, chunk in enumerate(chunks)
                ]
                self._collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
                counts[rel] = len(chunks)
        if counts:
            total = sum(counts.values())
            logger.info(
                "rag: ingested %d chunks from %d files", total, len(counts)
            )
        return counts

    def search(self, query: str, k: int = 3) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        result = self._collection.query(
            query_texts=[query], n_results=max(1, min(k, 10))
        )
        out: list[dict[str, Any]] = []
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for doc, meta, dist in zip(docs, metas, distances):
            out.append(
                {
                    "source": meta.get("source"),
                    "chunk_index": meta.get("chunk_index"),
                    "score": round(1 - dist, 4) if dist is not None else None,
                    "text": doc,
                }
            )
        return out
