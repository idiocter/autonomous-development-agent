# Short commands for everyday use. Run `make` on its own to see them all.
#
# `uv run` handles the virtualenv, so nothing needs activating first.

UV_EXTRAS = --extra dev --extra github --extra db --extra sandbox --extra rag --extra api
SANDBOX_IMAGE = autonomous-dev-agent-sandbox:latest
DB_CONTAINER = autonomous-dev-agent-postgres

.DEFAULT_GOAL := help
.PHONY: help install up down demo fix serve test clean

help:  ## show these commands
	@echo "Commands:"
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  make %-9s %s\n", $$1, $$2}'
	@echo ""
	@echo "First time:  make install && make up && make demo"

install:  ## install python packages (run again after switching branches)
	uv sync $(UV_EXTRAS)

up:  ## start the database and build the test container
	docker-compose up -d postgres
	@printf "waiting for database"
	@until docker exec $(DB_CONTAINER) pg_isready -U agent >/dev/null 2>&1; do printf "."; sleep 1; done
	@echo " ready"
	uv run alembic upgrade head
	docker build -q -f docker/Dockerfile.sandbox -t $(SANDBOX_IMAGE) docker/ >/dev/null
	@echo "ready -- try: make demo"

down:  ## stop the database
	docker-compose down

demo:  ## fix the bug in the built-in practice repo (safe, nothing leaves your machine)
	uv run python scripts/run_local_job.py \
		--repo tests/fixtures/toy_repo \
		--issue "Fix the off-by-one bug in calculate_total() -- it skips the last item"

fix:  ## fix a real GitHub issue: make fix REPO=you/your-repo ISSUE=4
	@test -n "$(REPO)"  || (echo "Usage: make fix REPO=you/your-repo ISSUE=4"; exit 1)
	@test -n "$(ISSUE)" || (echo "Usage: make fix REPO=you/your-repo ISSUE=4"; exit 1)
	uv run python scripts/run_github_job.py --repo $(REPO) --issue $(ISSUE)

serve:  ## start the web server (then open http://localhost:8000/docs)
	uv run uvicorn src.api.main:app --reload

test:  ## run the tests
	uv run pytest

clean:  ## delete old job working folders
	rm -rf workspaces/*
	@echo "cleared workspaces/"
