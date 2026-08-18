# Autonomous Software Development Agent

Takes a GitHub issue and works on it end-to-end: understand repo -> plan -> code -> test -> debug -> commit/PR.

See [`plan.md`](./plan.md) for the full architecture and phased build order. All 6 phases are code-complete;
see plan.md for exactly what's been verified against real local infra vs. what's blocked on credentials.

## Setup

```bash
uv sync --extra dev --extra github --extra db --extra sandbox --extra rag --extra api
cp .env.example .env
# fill in ANTHROPIC_API_KEY (required for everything), GITHUB_TOKEN (Phase 2+),
# GITHUB_WEBHOOK_SECRET (Phase 5+) in .env
```

### Postgres + pgvector

```bash
docker-compose up -d postgres   # remaps host port to 5433 -- 5432 may already be in use locally
uv run alembic upgrade head
```

### Docker sandbox image (Phase 3+)

```bash
docker build -f docker/Dockerfile.sandbox -t autonomous-dev-agent-sandbox:latest docker/
```

## Demos

**Phase 1 -- local toy repo, no GitHub/Docker/Postgres required for the graph shape itself:**
```bash
uv run python scripts/run_local_job.py --repo tests/fixtures/toy_repo --issue "Fix the off-by-one bug in calculate_total()"
```

**Phase 2 -- real GitHub issue, opens a real PR** (needs `GITHUB_TOKEN` + a scratch/test repo):
```bash
uv run python scripts/run_github_job.py --repo owner/repo --issue 42
```

**Phase 4 -- index a repo into the RAG vector store:**
```bash
uv run python scripts/index_repo.py --repo tests/fixtures/toy_repo --repo-url toy-repo-demo
```

**Phase 5 -- run the FastAPI webhook server:**
```bash
uv run uvicorn src.api.main:app --reload
# GET /health, GET /jobs, GET /jobs/{id}, GET /jobs/{id}/events (SSE), POST /webhooks/github
```
Use ngrok or smee.io to relay real GitHub webhook deliveries to `localhost:8000/webhooks/github` for local dev.

## Tests

```bash
uv run pytest
```
Requires the Postgres container running (`docker-compose up -d postgres`) and the sandbox image built for
the full suite; tests that need Docker/Postgres skip gracefully if those aren't available, except the
integration suite which assumes Postgres is reachable.
