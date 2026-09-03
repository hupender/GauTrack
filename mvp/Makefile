# ---------------------------------------------------------------------------
# GauTrack MVP
#
#   make dev          start dev Postgres, migrate, seed (SEED_DEMO=1), run API on 0.0.0.0:8000
#   make dev-tls      HTTPS front end for demoing the PWA on a phone over the LAN
#   make test         run the pytest suite (§7 of SPEC.md)
#   make verify-chain walk the audit + event hash chains
#   make help         list every target
#
# NOTE: this project's path can contain spaces, so every path is a *quoted*
# shell word inside the recipes, and no target or prerequisite is ever a path.
# GNU make cannot handle whitespace in target names at all.
# ---------------------------------------------------------------------------
SHELL      := /bin/bash
ROOT       := $(CURDIR)
VENV       := $(ROOT)/.venv
PY         := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
UVICORN    := $(VENV)/bin/uvicorn
PYTEST     := $(VENV)/bin/pytest
ALEMBIC    := $(VENV)/bin/alembic
API_DIR    := $(ROOT)/api
HOST       ?= 0.0.0.0
PORT       ?= 8000
SERVE      := cd "$(API_DIR)" && "$(UVICORN)" main:app --host $(HOST) --port $(PORT) --reload

# python3.12 is what the spec pins; fall back to Homebrew's if it is not on PATH.
PYTHON312  := $(shell command -v python3.12 2>/dev/null || echo /opt/homebrew/opt/python@3.12/bin/python3.12)

.PHONY: help dev run dev-tls lan-ip db-up db-down db-reset db-psql venv env status api-stop analyst-user runbook \
        walkthrough docs \
        migrate revision seed reseed reseed-new-passwords set-password test audit verify-chain anchor backup restore vendor \
        vendor-verify stop clean

help: ## list every target
	@grep -hE '^[a-z][a-z-]*:.*?##' "$(firstword $(MAKEFILE_LIST))" \
	  | sed -E 's/:[^#]*##[[:space:]]*/\t/' | sort | expand -t 22

# --------------------------------------------------------------------------- setup
venv: ## create mvp/.venv and install the pinned dependencies
	@if [ ! -x "$(PY)" ]; then \
	    echo "[venv] creating $(VENV)"; \
	    "$(PYTHON312)" -m venv "$(VENV)"; \
	    "$(PIP)" install --quiet --upgrade pip wheel; \
	    "$(PIP)" install --quiet -r "$(API_DIR)/requirements.txt"; \
	  fi

env: ## generate .env with fresh random secrets (never overwrites)
	@bash "$(ROOT)/scripts/make_env.sh"

# --------------------------------------------------------------------------- database
db-up: ## start the development Postgres (docker if available, else pg_ctl)
	@bash "$(ROOT)/scripts/dev_db.sh" start

db-down: ## stop the development Postgres
	@bash "$(ROOT)/scripts/dev_db.sh" stop

db-reset: ## DESTROY the development database and its data
	@bash "$(ROOT)/scripts/dev_db.sh" reset

db-psql: ## open a psql shell on the development database
	@bash "$(ROOT)/scripts/dev_db.sh" psql

migrate: venv env db-up ## apply database migrations (runs as the schema owner)
	@cd "$(API_DIR)" && "$(ALEMBIC)" upgrade head

revision: venv env ## create a migration: make revision M="message"
	@cd "$(API_DIR)" && "$(ALEMBIC)" revision -m "$(M)"

seed: migrate ## load the demo dataset; writes mvp/.seed_credentials.txt
	@cd "$(API_DIR)" && "$(PY)" -m seed

reseed: migrate ## wipe and reload the demo dataset (KEEPS existing passwords)
	@cd "$(API_DIR)" && "$(PY)" -m seed --force

reseed-new-passwords: migrate ## wipe, reload AND reissue every password
	@cd "$(API_DIR)" && "$(PY)" -m seed --force --new-passwords

set-password: venv env ## set one account's password: make set-password U=dmc
	@if [ -z "$(U)" ]; then echo "usage: make set-password U=<username>"; exit 1; fi
	@cd "$(API_DIR)" && "$(PY)" -m set_password "$(U)"

# --------------------------------------------------------------------------- run
dev: seed ## full local bring-up: db + migrations + demo data + API on :8000
	@echo ""
	@echo "  admin dashboard : http://localhost:$(PORT)/admin"
	@echo "  field PWA       : http://localhost:$(PORT)/app"
	@echo "  CM view         : http://localhost:$(PORT)/cm"
	@echo "  public report   : http://localhost:$(PORT)/report"
	@echo "  credentials     : $(ROOT)/.seed_credentials.txt"
	@echo ""
	@echo "  For a phone on the same Wi-Fi, run 'make lan-ip' in another terminal."
	@echo ""
	@$(SERVE)

run: venv env db-up ## run the API without reloading the demo data
	@$(SERVE)

dev-tls: ## HTTPS front end on :8443 for the phone demo (2nd terminal, after 'make dev')
	@bash "$(ROOT)/scripts/dev_tls.sh"

lan-ip: ## print the URL to open on the phone
	@bash "$(ROOT)/scripts/lan_ip.sh"

stop: api-stop db-down ## stop EVERYTHING (web server + database)

api-stop: ## stop the web server if it is still running on $(PORT)
	@pids=$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null); \
	if [ -n "$$pids" ]; then \
	  echo "[stop] stopping web server on port $(PORT) (pid: $$pids)"; \
	  kill $$pids 2>/dev/null || true; sleep 1; \
	  pids=$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null); \
	  [ -n "$$pids" ] && kill -9 $$pids 2>/dev/null || true; \
	else echo "[stop] no web server on port $(PORT)"; fi

status: ## show what is running (web server, database)
	@printf "web server (:$(PORT)) : "; \
	  lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t >/dev/null 2>&1 \
	  && echo "RUNNING (pid $$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t | tr '\n' ' '))" || echo "stopped"
	@printf "database          : "; bash "$(ROOT)/scripts/dev_db.sh" status >/dev/null 2>&1 \
	  && echo "RUNNING" || echo "stopped"

runbook: venv ## rebuild the three text PDFs (runbook, architecture, questions)
	@"$(PY)" -m pip install --quiet markdown pypdf reportlab
	@"$(PY)" "$(ROOT)/scripts/build_runbook_pdf.py"

walkthrough: venv ## rebuild WALKTHROUGH.html and GauTrack_Screens.pdf from .build/screens
	@"$(PY)" "$(ROOT)/scripts/build_walkthrough.py"

docs: runbook walkthrough ## rebuild every document

analyst-user: venv env ## create/reset the read-only DB login for Power BI / Excel
	@bash "$(ROOT)/scripts/analyst_user.sh"

# --------------------------------------------------------------------------- quality
test: venv env db-up ## run the test suite against a throwaway database
	@cd "$(ROOT)" && "$(PYTEST)" -q tests

audit: venv ## known-CVE scan of the pinned dependencies (pip-audit) + Python security lint (bandit)
	@"$(PIP)" install --quiet pip-audit bandit
	@cd "$(ROOT)" && "$(VENV)/bin/pip-audit" -r api/requirements.txt
	@cd "$(ROOT)" && "$(VENV)/bin/bandit" -q -r api -x api/alembic,api/seed.py -ll -ii

verify-chain: venv env ## walk the audit_log + events hash chains
	@cd "$(ROOT)" && "$(PY)" scripts/verify_chain.py

anchor: venv env ## print today's chain tip hash (publish this daily)
	@cd "$(ROOT)" && "$(PY)" scripts/anchor.py

# --------------------------------------------------------------------------- ops
backup: ## pg_dump + photos tarball into ./backups
	@bash "$(ROOT)/scripts/backup.sh"

restore: ## restore a backup: make restore F=backups/gautrack-YYYYmmdd-HHMM.sql.gz
	@bash "$(ROOT)/scripts/restore.sh" "$(F)"

vendor: ## re-download the pinned front-end libraries and rewrite CHECKSUMS.txt
	@bash "$(ROOT)/scripts/vendor_libs.sh"

vendor-verify: ## check the vendored libraries against CHECKSUMS.txt
	@bash "$(ROOT)/scripts/vendor_libs.sh" verify

clean: ## remove caches (keeps .venv, .pgdata, .env)
	@find "$(ROOT)" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; \
	  rm -rf "$(ROOT)/.pytest_cache"; echo "[clean] done"
