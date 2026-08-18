"""Similarity search against pgvector. Same output shape (list[RetrievedChunk])
as the Phase 1 grep stub it replaces (src/tools/rag_tools.py).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CodeChunk
from src.graph.state import RetrievedChunk
from src.rag.embeddings import embed_query


async def rag_retrieve(session: AsyncSession, repo_url: str, query: str, k: int = 8) -> list[RetrievedChunk]:
    query_vector = embed_query(query)
    result = await session.execute(
        select(CodeChunk)
        .where(CodeChunk.repo_url == repo_url)
        .order_by(CodeChunk.embedding.cosine_distance(query_vector))
        .limit(k)
    )
    chunks = result.scalars().all()
    return [
        RetrievedChunk(
            file_path=c.file_path, start_line=c.start_line, end_line=c.end_line, content=c.content
        )
        for c in chunks
    ]
