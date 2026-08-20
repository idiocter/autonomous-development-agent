"""`rag_retrieve` now indexes (if stale) and queries the real pgvector-backed
store built in Phase 4 (src/rag/indexer.py, src/rag/retriever.py), replacing
the Phase 1 naive-grep stub. `rag_retrieve_grep` is that original stub, kept
for tests/tooling that shouldn't need a running Postgres just to exercise
unrelated logic.
"""

import asyncio
import re

import structlog

from src.db.session import async_session_factory
from src.graph.state import RetrievedChunk
from src.rag import retriever as _retriever
from src.rag.indexer import index_repo
from src.tools.filesystem_tools import list_repo_structure

logger = structlog.get_logger(__name__)

_SKIP_SUFFIXES = (".pyc", ".png", ".jpg", ".lock")


def rag_retrieve(repo_root: str, query: str, k: int = 8, *, repo_url: str | None = None) -> list[RetrievedChunk]:
    """Synchronous wrapper: LangGraph nodes are plain sync functions, and
    graph.invoke() itself always runs off the asyncio event loop (a plain
    script's main thread, or job_runner's asyncio.to_thread worker thread),
    so there's never an already-running loop in the calling thread and
    asyncio.run() here is safe.
    """
    repo_url = repo_url or repo_root

    async def _run() -> list[RetrievedChunk]:
        async with async_session_factory() as session:
            await index_repo(session, repo_url, repo_root)
            return await _retriever.rag_retrieve(session, repo_url, query, k)

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 -- degrade, don't abort the job
        # Semantic retrieval needs pgvector; the grep fallback needs nothing.
        # Worse context beats a dead run -- the agent can still read files
        # through its own tools.
        logger.warning("pgvector retrieval unavailable, falling back to grep", error=str(exc))
        return rag_retrieve_grep(repo_root, query, k)


def rag_retrieve_grep(repo_root: str, query: str, k: int = 8) -> list[RetrievedChunk]:
    terms = [t.lower() for t in re.findall(r"[a-zA-Z_]{3,}", query)]
    if not terms:
        return []

    scored: list[tuple[int, RetrievedChunk]] = []
    for rel_path in list_repo_structure(repo_root):
        if rel_path.endswith(_SKIP_SUFFIXES):
            continue
        try:
            content = open(f"{repo_root}/{rel_path}").read()
        except (UnicodeDecodeError, OSError):
            continue

        lower = content.lower()
        score = sum(lower.count(term) for term in terms)
        if score == 0:
            continue

        lines = content.splitlines()
        scored.append(
            (
                score,
                RetrievedChunk(
                    file_path=rel_path,
                    start_line=1,
                    end_line=len(lines),
                    content=content[:2000],
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for _, chunk in scored[:k]]
