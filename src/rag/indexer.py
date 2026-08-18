"""Walks a repo, chunks files, embeds, and upserts into pgvector.

Incremental indexing: per file, compare the set of content_hashes the file
would produce now against what's already stored for that (repo_url,
file_path). Identical hash sets mean the file is unchanged -- skipped
entirely, no re-embedding or DB writes. Only changed/new files get their
old chunks deleted and new ones inserted; files no longer present in the
repo get their stale chunks removed too. This runs whenever HEAD has moved
(RepoIndexMeta.last_indexed_sha check), not on every call.
"""

import subprocess
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CodeChunk, RepoIndexMeta
from src.rag.chunking import chunk_file
from src.rag.embeddings import EMBEDDING_MODEL_NAME, embed_texts

_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_MAX_FILE_SIZE = 500_000
_TEXT_SUFFIXES = {".py", ".md", ".rst", ".txt", ".toml", ".yml", ".yaml", ".json", ".cfg", ".ini"}

# Hard denylist independent of .gitignore or the suffix filter above -- a
# repo might .gitignore its .env but the indexer shouldn't even consider
# reading it if it exists on disk. (Names like ".env" already have an empty
# Path.suffix so _TEXT_SUFFIXES excludes them naturally; this is explicit
# defense-in-depth, not the only thing standing between secrets and the
# vector store.)
_SECRET_DENYLIST_SUBSTRINGS = (".env", "id_rsa", ".pem", "credentials.json", "service_account")


def _is_denylisted(rel_path: str) -> bool:
    name = Path(rel_path).name.lower()
    return any(pattern in name for pattern in _SECRET_DENYLIST_SUBSTRINGS)


def _tracked_files(repo_root: str) -> list[Path]:
    root = Path(repo_root)
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, check=True
        )
        candidates = [root / p for p in result.stdout.splitlines()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a git repo (e.g. the toy-repo fixture) -- fall back to a plain
        # walk, still respecting the skip-dir denylist.
        candidates = [p for p in root.rglob("*") if p.is_file()]

    return [
        p
        for p in candidates
        if p.is_file()
        and p.suffix in _TEXT_SUFFIXES
        and not any(part in _SKIP_DIRS for part in p.parts)
        and p.stat().st_size <= _MAX_FILE_SIZE
    ]


def _repo_head_sha(repo_root: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "no-git"


async def index_repo(session: AsyncSession, repo_url: str, repo_root: str) -> int:
    head_sha = _repo_head_sha(repo_root)

    meta = await session.get(RepoIndexMeta, repo_url)
    if meta is not None and meta.last_indexed_sha == head_sha and head_sha != "no-git":
        return meta.chunk_count

    total_chunks = 0
    seen_files: set[str] = set()

    for path in _tracked_files(repo_root):
        rel_path = str(path.relative_to(repo_root))
        if _is_denylisted(rel_path):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        seen_files.add(rel_path)

        new_chunks = chunk_file(rel_path, content)
        new_hashes = {c.content_hash for c in new_chunks}

        result = await session.execute(
            select(CodeChunk.content_hash).where(
                CodeChunk.repo_url == repo_url, CodeChunk.file_path == rel_path
            )
        )
        existing_hashes = {row[0] for row in result.all()}

        if new_hashes == existing_hashes:
            total_chunks += len(new_chunks)
            continue  # unchanged -- skip re-embedding and re-writing this file

        await session.execute(
            delete(CodeChunk).where(CodeChunk.repo_url == repo_url, CodeChunk.file_path == rel_path)
        )
        if new_chunks:
            embeddings = embed_texts([c.content for c in new_chunks])
            for chunk, vector in zip(new_chunks, embeddings):
                session.add(
                    CodeChunk(
                        repo_url=repo_url,
                        file_path=chunk.file_path,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        chunk_type=chunk.chunk_type,
                        language=chunk.language,
                        content=chunk.content,
                        content_hash=chunk.content_hash,
                        embedding=vector,
                    )
                )
        total_chunks += len(new_chunks)

    # Files that were indexed before but no longer exist (deleted/renamed).
    result = await session.execute(
        select(CodeChunk.file_path).where(CodeChunk.repo_url == repo_url).distinct()
    )
    indexed_files = {row[0] for row in result.all()}
    stale_files = indexed_files - seen_files
    if stale_files:
        await session.execute(
            delete(CodeChunk).where(
                CodeChunk.repo_url == repo_url, CodeChunk.file_path.in_(stale_files)
            )
        )

    if meta is None:
        meta = RepoIndexMeta(
            repo_url=repo_url,
            last_indexed_sha=head_sha,
            chunk_count=total_chunks,
            embedding_model=EMBEDDING_MODEL_NAME,
        )
        session.add(meta)
    else:
        meta.last_indexed_sha = head_sha
        meta.chunk_count = total_chunks
        meta.embedding_model = EMBEDDING_MODEL_NAME

    await session.commit()
    return total_chunks
