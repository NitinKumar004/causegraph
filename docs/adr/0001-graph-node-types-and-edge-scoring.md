# ADR 0001 — Graph node types and edge-rule scoring

Status: accepted (causegraph-M3f-M4, 2026-08-16)

## Context

M3f-M4 introduces the first non-process node (FILE) and the first inferred (<1.0) edge
(`file_watch`). This forces two boundary decisions that every later rule (socket, cron) inherits.

## Decision

1. **The builder owns `event.kind → node` mapping; edge rules own `nodes → edges` only.**
   - Builder maps process kinds → process nodes (existing) and `file.change` → FILE nodes (new).
   - An `EdgeRule` only proposes `ProposedEdge`s between nodes the builder already created; it never
     creates nodes. So adding a new *rule* over existing node types (e.g. a temporal rule) is a new
     file with **no builder change** — the §6 seam holds for rules.
   - Adding a new *node type* (a new `kind`) IS a builder change, by design: the builder is the sole
     kind→node authority. This is honest and keeps the invariant in one place, rather than scattering
     node construction across rules (which would desync edges from nodes).

2. **Node keys have one owner each.** `model.ProcessNode.key = (pid, spawn_ts)` (existing);
   `model.file_key(path) = ("file", normpath(path))` (new) — normalized inside `file_key` — is used by
   BOTH the builder and `file_watch`, so the same path in any string form yields one key.
   `model.process_keys(g)` / `is_process_key(k)` is the single owner of the key-type guard that
   `attribution` routes through, so the heterogeneous-key (`(int,int)` vs `(str,str)`) comparison is
   never re-armed. `traverse.parent_of` uses the stronger *semantic* guard `rule=="spawn"` (a
   file_watch predecessor is never lineage, independent of key type) — both prevent a FILE key from
   entering the ancestry chain.

3. **Edge confidence = base × provenance × temporal tightness, provenance = min of the edge's two
   endpoints.** A `file_watch` edge spans an fsnotify file.change and a poll-observed spawn; its
   provenance is `min(fsnotify, poll)` — an edge is only as trustworthy as its weaker end. `dt` is
   measured against `spawn_ts`, which is poll-grained (up to one sample interval late), so the
   temporal `half_life` (default 60s) must exceed the sample interval (2s poll). Distinct from the
   `file_watch` eligibility `window` (default 30s), which bounds *which* changes are even considered.

## Consequences

- The precision-over-recall guarantee (§5.3) comes primarily from `file_watch`'s **path-reference
  match** (the file must appear in the process's exe/absolute-args), NOT from `min_confidence` — the
  latter only drops weak edges, not coincidental references. A coincidental reference can still score
  >0.5, so the match is the real guard.
- Reversible: making `actor` optional, adding `actor.cwd`, or a `ProposedNode` seam extension are all
  future options if node-producing rules proliferate.
