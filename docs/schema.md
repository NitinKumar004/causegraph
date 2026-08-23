# The event contract

Everything CauseGraph records is one canonical **event**. The recorder (Go) and the engine
(Python) are two separate programs, so this shape is the single contract between them — it is
defined once and both sides are generated from it, so they can never drift.

Above the recorder, code only ever looks at an event's `kind`; it never knows or cares which
operating system produced it.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string | unique id for the event |
| `ts` | int64 | timestamp, nanoseconds |
| `host_id` | string | which machine (room to grow to many machines later) |
| `kind` | enum | `heartbeat` · `process.spawn` · `process.exit` · `resource.sample` · `file.change` |
| `actor` | object | the process the event is about — `pid, ppid, exe, args[], user` |
| `target` | object? | optional — a `path` or `socket` the event refers to |
| `metrics` | object? | optional — `cpu_pct`, `rss_bytes`, `temp_c` (for `resource.sample`) |
| `source` | enum | how it was captured (`poll`, and native backends later) — used when scoring confidence |
| `confidence` | number | `1.0` for observed facts; below `1.0` for inferred causal links |

Optional fields are **omitted, not null**, when absent, so both languages serialize an event
to exactly the same bytes.

## Notes

- The engine reads events in write order and only needs the event's id and its JSON body, so it
  can read any recording the recorder produced, including older ones.
- The store keeps a version marker, leaving room for additive changes to the schema without
  breaking existing recordings.
