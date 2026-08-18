"""Phase 4 CLI: manually trigger/inspect RAG indexing for a repo path.

Usage:
    uv run python scripts/index_repo.py --repo tests/fixtures/toy_repo
    uv run python scripts/index_repo.py --repo workspaces/job-xxx/widgets --repo-url owner/widgets
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.session import async_session_factory  # noqa: E402
from src.rag.indexer import index_repo  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Index a repo into the RAG vector store.")
    parser.add_argument("--repo", required=True, help="local path to the repo")
    parser.add_argument("--repo-url", default=None, help="stable identifier; defaults to --repo path")
    args = parser.parse_args()

    repo_url = args.repo_url or args.repo
    async with async_session_factory() as session:
        count = await index_repo(session, repo_url, args.repo)
    print(f"indexed {count} chunks for repo_url={repo_url}")


if __name__ == "__main__":
    asyncio.run(main())
