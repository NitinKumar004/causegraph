# CauseGraph — M0–M2. Two languages, one contract.
ROOT    := $(shell pwd)
PY      := $(ROOT)/engine/.venv/bin/python
PYTEST  := $(ROOT)/engine/.venv/bin/pytest

.PHONY: all gen check-gen venv build build-go test test-go test-py fixtures clean

all: build

## gen: regenerate Go structs + Python dataclasses from the schema
gen:
	python3 shared/schema/gen.py

## check-gen: fail if generated code is stale vs the schema (drift gate, AC1)
check-gen:
	python3 shared/schema/gen.py --check

## venv: create the engine virtualenv and install pinned deps
venv:
	python3 -m venv engine/.venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q networkx==3.6.1 pytest==8.3.4

build-go:
	go -C daemon build -o $(ROOT)/bin/cged ./cmd/cged

## build: compile the daemon and ensure the engine venv exists
build: build-go
	@test -x $(PY) || $(MAKE) venv
	@chmod +x scripts/cg
	@echo "built bin/cged and scripts/cg"

## test: the full gate — schema drift + Go (race) + Python (incl. seam integration)
test: check-gen build-go test-go test-py

test-go:
	go -C daemon test -race ./...

## scale: run the write + query/retention scale harness into .evidence/artifacts/
scale: build-go
	@mkdir -p .evidence/artifacts
	go -C daemon run ./cmd/scalebench -n 100000 > .evidence/artifacts/scale_write.json
	@test -x $(PY) || $(MAKE) venv
	PYTHONPATH=engine $(PY) scripts/scale_query.py --rows 500000 --json .evidence/artifacts/scale_query.json >/dev/null
	@echo "wrote .evidence/artifacts/scale_write.json + scale_query.json"

test-py:
	@test -x $(PYTEST) || $(MAKE) venv
	cd engine && PYTHONPATH=. $(PYTEST) -q

## fixtures: build demo DBs from the committed fixtures
fixtures: build
	./scripts/cg load test/fixtures/graph_events.jsonl --db test/fixtures/graph.db
	./scripts/cg load test/fixtures/why_events.jsonl --db test/fixtures/why.db
	@echo "wrote test/fixtures/graph.db + why.db"

clean:
	rm -rf bin *.db *.db-wal *.db-shm test/fixtures/graph.db
