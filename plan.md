# Autonomous Software Development Agent — Plan

An AI developer that takes a GitHub issue and works on it end-to-end, fully autonomously, with real GitHub integration.

**Flow:** GitHub issue → understand repo → inspect code → plan → modify files → run tests → debug → commit/PR.

**Tech stack:** Python (3.11/3.12 pinned via `uv`/`pyenv`), LangGraph, FastAPI, Anthropic SDK (`claude-opus-5` for Planner/Debugger, `claude-sonnet-5` for Coding/Testing), PyGithub + GitPython, Docker (`docker-py` SDK), PostgreSQL + pgvector, SQLAlchemy + Alembic.

## Architecture

- **FastAPI** is ingress-only: `/webhooks/github` (HMAC-verified issue events), `/jobs` (status/cancel/retry REST), `/health`. Never does agent work inline in the request cycle — enqueues to a background worker.
- **Worker** (in-proc `asyncio.Queue` through Phase 4; Celery+Redis is a documented Phase 6 upgrade) clones the repo, triggers RAG indexing if stale, invokes the LangGraph graph.
- **LangGraph graph** (`src/graph/build_graph.py`, state in `src/graph/state.py`): nodes `planner → coding → testing → (debugging ⟲ coding | pr_creation | human_escalation)`. Shared `AgentState` TypedDict carries issue context, plan steps, diffs, test results/history, iteration count, cost tracking.
- **Tools** (plain, independently testable functions in `src/tools/`): `github_tools.py` (PyGithub — issue/PR/comment ops), `filesystem_tools.py` (path-traversal-guarded read/write/grep scoped to job workspace), `sandbox_tools.py` (Docker exec wrapper), `rag_tools.py` (pgvector similarity search).
- **Docker sandbox** (`src/sandbox/docker_manager.py`): editing happens on the host workspace filesystem; only **execution** (tests/lint/arbitrary shell) runs inside an isolated, resource-limited (`mem_limit`, `nano_cpus`, `pids_limit`, `network_mode="none"` by default, non-root user), timeout-enforced container. Always `container.remove(force=True)` in a `finally`.
- **RAG** (`src/rag/`): AST-aware chunking for Python (fallback to line-window for other languages), embeddings via Voyage AI (`voyage-code-3`) or OpenAI, stored in Postgres `code_chunks` table with an HNSW pgvector index. Different retrieval scope per node (Planner: broad; Coding: file-scoped; Debugging: traceback-scoped).
- **Loop-safety (four independent guards, all required):** hard iteration cap (default 6), repeated-failure-signature detection, wall-clock timeout per job (30–45 min) and per tool call, cumulative token/cost budget per job (abort + escalate on breach). Never rely on just one.
- **GitHub safety:** dedicated branch per job (`agent/issue-{number}-{job_id}`), never force-push over unexpected divergence, PR-only (never auto-merge by default), bot identity for commits, PAT scoped to the single target repo (GitHub App migration documented for later), idempotency check for existing open PRs before creating duplicates.
- **Data model (Postgres):** `jobs`, `tasks` (per-node audit trail), `events` (fine-grained log/SSE feed), `repo_index_meta`, `code_chunks`, `test_results`. Use LangGraph's `PostgresSaver` checkpointer for free job resumability.

## Folder structure

```
src/
├── config.py, logging_config.py
├── api/            (main.py, routers/webhooks.py, jobs.py, health.py, schemas.py, deps.py)
├── graph/          (state.py, build_graph.py, routing.py, nodes/{planner,coding,testing,debugging,pr_creation,escalation}.py)
├── agents/         (base.py, planner_agent.py, coding_agent.py, testing_agent.py, debugging_agent.py)
├── tools/          (github_tools.py, filesystem_tools.py, sandbox_tools.py, rag_tools.py)
├── sandbox/        (docker_manager.py, Dockerfile.sandbox, resource_limits.py)
├── rag/            (indexer.py, chunking.py, embeddings.py, retriever.py)
├── db/             (models.py, session.py, crud.py, migrations/)
├── worker/         (queue.py, job_runner.py, celery_app.py [later])
└── github_integration/ (webhook_handler.py, auth.py)
docker/ (api.Dockerfile, worker.Dockerfile, postgres/init.sql)
scripts/ (seed_toy_repo.py, run_local_job.py, index_repo.py)
tests/ (unit/, integration/, fixtures/toy_repo/)
docker-compose.yml, .env.example, pyproject.toml, Makefile, README.md
```

## Phased build order (each phase ends in something demonstrable)

Status: **all 6 phases code-complete and verified** wherever verification doesn't require credentials that haven't been supplied yet (`ANTHROPIC_API_KEY`, a real `GITHUB_TOKEN` + scratch repo). Every phase's non-LLM machinery (Docker sandbox, Postgres/pgvector, RAG indexing/retrieval, FastAPI + webhook HMAC/dedup, SSE) has been exercised against real local infra (Docker Desktop, the `pgvector/pgvector:pg16` container) with 53/53 tests passing.

1. **Skeleton graph, stubbed tools, toy local repo** — full 6-node graph wired for real Anthropic calls (blocked on API key to actually invoke), `scripts/run_local_job.py --repo tests/fixtures/toy_repo --issue "..."` ready to run.
2. **Real GitHub read/write** — `src/github_integration/auth.py` (PAT + a code-complete GitHub App JWT provider), `src/tools/github_tools.py` (branch/commit/push via GitPython, idempotent PR creation, escalation comments), `scripts/run_github_job.py`. Unit-tested with a real throwaway git repo + mocked PyGithub calls (9/9 passing); live PR creation needs a real token + scratch repo.
3. **Docker sandbox execution** — `src/sandbox/docker_manager.py` built and verified for real: detects the toy repo's bug, confirms the fix, enforces timeouts, blocks network access, leaks zero containers (4/4 tests passing against a real built image). All four loop-safety guards (iteration cap, repeated-failure detection, cost budget via `src/agents/usage.py`, wall-clock timeout via `src/worker/job_runner.py`) verified with mocked graph runs against real Postgres.
4. **RAG** — `src/rag/{chunking,embeddings,indexer,retriever}.py`, local `sentence-transformers` (no API key needed) + real pgvector. Verified end-to-end: semantic retrieval correctly surfaces the actual buggy function from a natural-language query (10/10 tests passing).
5. **FastAPI webhook server** — `src/api/` (webhooks/jobs/health routers) + `src/worker/queue.py` (asyncio-based, job-prep kept out of the request cycle). HMAC verification, label-gating, delivery-ID dedup, and the jobs REST API all verified against a live `uvicorn` server and real Postgres (18/18 tests passing).
6. **Hardening** — GitHub App auth provider (code-complete), true incremental RAG re-indexing (per-file content-hash diffing, verified: unchanged files keep identical row IDs, changed files get new ones), an explicit secrets denylist in the indexer (verified: `.env` content never reaches the vector store even if present on disk), and a polling-based SSE endpoint at `/jobs/{id}/events` (verified).

## Key risks to design around from day one

- Unsafe shell/git ops — contain via sandbox isolation + path-scoped filesystem tools + GitHub-tool-only git writes.
- Infinite/expensive retry loops — four-guard system above.
- Prompt injection via issue body/repo content — never in system prompt; restrict trigger label to maintainer-addable; scoped token.
- Secrets denylist for RAG/filesystem tools independent of `.gitignore`.
- Structured-output parsing for plan steps — use JSON schema, not prose "return JSON".
- Webhook replay dedup via `X-GitHub-Delivery` header.

## Verification

Each phase has a runnable demo command (`scripts/run_local_job.py` locally, then against a real scratch GitHub repo/issue) — verify by actually running it and inspecting the produced diff/PR/report, plus `pytest` for unit tests on tools/routing logic and an integration test against the toy repo fixture with real Docker.
