# Event schema

`shared/schema/event.schema.json` is the single source of truth (architecture.md §3.3).
Everything above the capture layer sees only canonical `Event`s and branches on `kind`,
never on OS.

## Codegen

`make gen` runs `shared/schema/gen.py`, which emits:

- `daemon/internal/event/event_gen.go` (package `event`, gofmt'd)
- `engine/causegraph/schema/event_gen.py` (dataclasses)

`make check-gen` fails if either is stale — the drift gate (tested in
`engine/tests/test_schema_drift.py`). Never hand-edit the generated files.

## Fields

| Field | Type | Notes |
|---|---|---|
| `id` | string | uuid |
| `ts` | int64 | nanoseconds since epoch |
| `host_id` | string | enables fleet mode later |
| `kind` | enum | `heartbeat` · `process.spawn` · `process.exit` · `resource.sample` |
| `actor` | object | `pid, ppid, exe, args[], user` — always present |
| `target` | object? | `path?, socket?` — optional (file/socket events, M3+) |
| `metrics` | object? | `cpu_pct?, rss_bytes?, temp_c?` — for `resource.sample` |
| `source` | enum | `poll` (M0–M2) · `ebpf` · `es` · `etw` — provenance for scoring |
| `confidence` | number | 1.0 for observed facts; < 1 reserved for inferred edges (M4) |

Optional fields are omitted (not null) when absent, so Go and Python serialize identically.
Cross-language fidelity is pinned by `test/fixtures/events.jsonl`, decoded to the same values
by `daemon/internal/event/codec_test.go` and `engine/tests/test_codec_roundtrip.py`.

## Storage

SQLite `events(seq, ts, pid, ppid, kind, data)` where `data` is the canonical JSON. The engine
reader depends only on `seq` + `data`, so it reads any DB the daemon wrote. A `meta` row
records `schema_version` for future additive migrations.
