"""Exercises the real indexer -> pgvector -> retriever pipeline against the
live Postgres container and the local sentence-transformers embedding
model (no external API key needed for either).
"""

import shutil
import subprocess
import uuid

from sqlalchemy import select

from src.db.models import CodeChunk, RepoIndexMeta
from src.db.session import async_session_factory
from src.rag.indexer import index_repo
from src.rag.retriever import rag_retrieve

_SCRATCH_ROOT = "workspaces/test-scratch-rag"


async def test_index_repo_indexes_workspace_nested_inside_another_git_repo(tmp_path):
    """Regression: a job workspace nested inside an unrelated git repo that
    gitignores it made `git ls-files` exit 0 with empty output, so the
    indexer silently indexed nothing instead of walking the directory.
    """
    outer = tmp_path / "outer_repo"
    outer.mkdir()
    subprocess.run(["git", "init"], cwd=outer, check=True, capture_output=True)
    (outer / ".gitignore").write_text("workspaces/\n")

    nested = outer / "workspaces" / "job-abc123" / "toy_repo"
    nested.mkdir(parents=True)
    shutil.copytree("tests/fixtures/toy_repo", nested, dirs_exist_ok=True)

    repo_url = f"test-nested-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session:
        count = await index_repo(session, repo_url, str(nested))

        result = await session.execute(select(CodeChunk).where(CodeChunk.repo_url == repo_url))
        rows = result.scalars().all()

    assert count > 0, "nested workspace indexed zero files -- git ls-files fallback regressed"
    assert any(r.file_path == "calculator.py" for r in rows)


async def test_nested_workspace_head_sha_does_not_leak_from_outer_repo(tmp_path):
    """Regression: _repo_head_sha resolved the *outer* repo's HEAD for a
    nested workspace, so index_repo's stale-check short-circuited on an
    unrelated SHA and never picked up edits (and every workspace shared
    one SHA). Two distinct workspaces must not report the same sha.
    """
    from src.rag.indexer import _repo_head_sha

    outer = tmp_path / "outer_repo"
    outer.mkdir()
    subprocess.run(["git", "init"], cwd=outer, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=outer, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=outer, check=True)
    (outer / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=outer, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=outer, check=True, capture_output=True)

    nested = outer / "workspaces" / "job-abc" / "repo"
    nested.mkdir(parents=True)

    assert _repo_head_sha(str(nested)) == "no-git"
    assert _repo_head_sha(str(outer)) != "no-git"  # a real git root still resolves


async def test_index_repo_creates_chunks_and_meta():
    repo_url = f"test-repo-{uuid.uuid4().hex[:8]}"

    async with async_session_factory() as session:
        count = await index_repo(session, repo_url, "tests/fixtures/toy_repo")
        assert count > 0

        meta = await session.get(RepoIndexMeta, repo_url)
        assert meta is not None
        assert meta.chunk_count == count

        result = await session.execute(select(CodeChunk).where(CodeChunk.repo_url == repo_url))
        rows = result.scalars().all()
        assert len(rows) == count
        assert any(r.file_path == "calculator.py" for r in rows)


async def test_index_repo_skips_reindex_when_sha_unchanged():
    repo_url = f"test-repo-{uuid.uuid4().hex[:8]}"

    async with async_session_factory() as session:
        first_count = await index_repo(session, repo_url, "tests/fixtures/toy_repo")

        result = await session.execute(select(CodeChunk.id).where(CodeChunk.repo_url == repo_url))
        ids_before = {row[0] for row in result.all()}

        second_count = await index_repo(session, repo_url, "tests/fixtures/toy_repo")

        result = await session.execute(select(CodeChunk.id).where(CodeChunk.repo_url == repo_url))
        ids_after = {row[0] for row in result.all()}

    assert first_count == second_count
    # tests/fixtures/toy_repo has no .git of its own, so head_sha resolves
    # to "no-git" every call -- by design (see indexer.py), that always
    # skips the whole-repo short-circuit and goes through the per-file loop.
    # Per-file content-hash comparison means unchanged files are skipped
    # entirely (no delete+reinsert), so the row IDs themselves should be
    # identical across the two calls, not just the count.
    assert ids_before == ids_after


async def test_index_repo_reembeds_only_changed_files():
    repo_url = f"test-repo-{uuid.uuid4().hex[:8]}"
    scratch = f"{_SCRATCH_ROOT}/{uuid.uuid4().hex[:8]}"
    shutil.copytree("tests/fixtures/toy_repo", scratch)
    try:
        async with async_session_factory() as session:
            await index_repo(session, repo_url, scratch)

            result = await session.execute(
                select(CodeChunk.id, CodeChunk.file_path).where(CodeChunk.repo_url == repo_url)
            )
            ids_before = {(row.id, row.file_path) for row in result.all()}

            # Edit a line INSIDE the function body -- AST-based chunking
            # (chunk_python_file) captures only the function's own line
            # span, so appending content *after* the function wouldn't
            # register as a change to its chunk.
            calculator_path = f"{scratch}/calculator.py"
            original = open(calculator_path).read()
            open(calculator_path, "w").write(original.replace("total = 0.0", "total = 0.0  # start"))

            await index_repo(session, repo_url, scratch)

            result = await session.execute(
                select(CodeChunk.id, CodeChunk.file_path).where(CodeChunk.repo_url == repo_url)
            )
            ids_after = {(row.id, row.file_path) for row in result.all()}

        unchanged_before = {i for i, p in ids_before if p != "calculator.py"}
        unchanged_after = {i for i, p in ids_after if p != "calculator.py"}
        changed_before = {i for i, p in ids_before if p == "calculator.py"}
        changed_after = {i for i, p in ids_after if p == "calculator.py"}

        # Untouched files (README.md, test_calculator.py) keep the exact
        # same row IDs -- proof they were never re-embedded.
        assert unchanged_before == unchanged_after
        # The edited file's chunk(s) got new IDs -- proof it WAS re-embedded.
        assert changed_before != changed_after
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def test_index_repo_never_indexes_env_files():
    repo_url = f"test-repo-{uuid.uuid4().hex[:8]}"
    scratch = f"{_SCRATCH_ROOT}/{uuid.uuid4().hex[:8]}"
    shutil.copytree("tests/fixtures/toy_repo", scratch)
    try:
        with open(f"{scratch}/.env", "w") as f:
            f.write("ANTHROPIC_API_KEY=sk-super-secret-should-never-be-indexed\n")

        async with async_session_factory() as session:
            await index_repo(session, repo_url, scratch)

            result = await session.execute(select(CodeChunk).where(CodeChunk.repo_url == repo_url))
            rows = result.scalars().all()

        assert all(".env" not in r.file_path for r in rows)
        assert all("super-secret" not in r.content for r in rows)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def test_retrieval_finds_semantically_relevant_chunk():
    repo_url = f"test-repo-{uuid.uuid4().hex[:8]}"

    async with async_session_factory() as session:
        await index_repo(session, repo_url, "tests/fixtures/toy_repo")

        chunks = await rag_retrieve(
            session, repo_url, "off-by-one bug that skips the last item in a list of prices", k=3
        )

    assert len(chunks) > 0
    assert any(c["file_path"] == "calculator.py" for c in chunks)


async def test_retrieval_is_scoped_to_repo_url():
    repo_a = f"test-repo-a-{uuid.uuid4().hex[:8]}"
    repo_b = f"test-repo-b-{uuid.uuid4().hex[:8]}"

    async with async_session_factory() as session:
        await index_repo(session, repo_a, "tests/fixtures/toy_repo")
        # repo_b never indexed -- retrieval against it should find nothing,
        # proving results aren't leaking across repo_url boundaries.
        chunks = await rag_retrieve(session, repo_b, "calculate_total prices", k=5)

    assert chunks == []
