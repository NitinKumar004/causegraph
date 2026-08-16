# causegraph-M6 — read-only web UI (causal-graph viewer)

> Contract. A local page that visualizes the causal graph the engine already builds. The UI adds
> NO reasoning — it serializes the graph to JSON and renders it. Divergences tagged `[A]`.

## Goal

`cg ui --db <path>` serves a local page at `127.0.0.1:<port>` where you enter a pid or a question
and see the causal graph — process ancestry + file causes, process vs file nodes styled
differently, edges labelled/coloured by rule and confidence — rendered with cytoscape.js, offline.

## Out of scope

- Live/streaming updates from a running daemon (reads a DB snapshot only). Auth. Any write/mutation
  endpoint. Full visual/screenshot regression (no browser harness here — visual look is manual).

## Shape

```text
 browser (127.0.0.1)                          engine
   |  GET / , /app.js , /vendor/cytoscape.min.js  <---- static files under ui/ *
   |  GET /api/graph?pid=<pid>   or  ?q=<question>   (NO db in the URL)
   v
 api/server.py *  graph_payload(db, pid|q) --> reader[R] -> builder[R] -> attribution[R]
   |   (db is bound ONCE at `cg ui --db` startup,      |  resolve[R] (q) / latest_instance[R] (pid)
   |    closed over by the Handler — never per-request) v  neighborhood: ancestry_path + subtree + causes
   +--> JSON {nodes:[{id,kind,label,pid?,exe?,...}], edges:[{source,target,rule,confidence}]}
   ^ 127.0.0.1 only, read-only (no mutation routes)          cg ui * launches serve()
```

## Precedent

- `[R]` reader / builder / attribution / traverse / resolver / model / cli pattern — `.evidence/precedent.md`.
- `[A]` stdlib `http.server` (no web framework) — read-only local routing is trivial; keeps deps light.
- `[A]` bind `127.0.0.1` only, read-only — a tool serving local process data must not expose remotely.
- `[A]` cytoscape.js v3.30.2 vendored offline (`ui/vendor/`) — local-first/testable; not a CDN.

## Unknowns → spikes

- none — pure serialization over proven engine APIs + a static page.

## Acceptance criteria

1. `graph_payload(db, pid=None, q=None, min_confidence=0.5, max_nodes=500) -> dict` is a PURE function
   (no HTTP). Node set is built with a **bound** so no single culprit can serialize the whole graph:
   the mandatory core is `{culprit} ∪ ancestry_path` (genuinely bounded — a single root-ward path);
   then the remaining `max_nodes` budget is filled in this deterministic order — (a) file `causes`
   sorted most-confident-first, then (b) `subtree` descendants in **BFS order** — stopping at
   `max_nodes`. If ANY cause OR descendant was dropped, set `"truncated": true`. All deduplicated by id
   (`ancestry_path`/`subtree` both include the culprit). Edges = every `(u,v)` in `g.edges()` where
   BOTH ends are in the final node set (spawn + file_watch edges), carrying their stored
   rule/confidence. File causes are pre-filtered to `>= min_confidence`.
2. Node JSON: process → `{id:"p:<pid>:<spawn_ts>", kind:"process", label:"<exe or '?'> (pid <pid>)"
   (a UI-specific label; NOT the same string as `cli._node_line`), pid, exe, user, observed_spawn,
   peak_cpu_pct, peak_rss_bytes}`; file →
   `{id:"f:<path>", kind:"file", label:<basename>, path}`. Edge JSON: `{source, target, rule,
   confidence}` with ids matching node ids. IDs are stable + collision-free.
3. `?q=` resolves via `resolver.resolve`; on failure (empty / absent-pid / no data) → `{"error":
   "<message>"}` + HTTP 400, never a 500/traceback.
3a. Neither `pid` nor `q` → `{"error":"pid or q required"}` + 400; both → `{"error":"pid and q are
   mutually exclusive"}` + 400; non-int `pid` → 400. Optional `?min_confidence=<float>` (default 0.5,
   mirrors `cg why`); malformed → 400.
4. `?pid=` uses `latest_instance`; an absent pid → `{"error":…}` + 400.
5. `serve(host, port, db)` binds `127.0.0.1` only and uses `http.server.ThreadingHTTPServer` (static
   and API requests don't serialize behind one slow query). Routes: `GET /` → 200 text/html; `GET
   /app.js` → 200 text/javascript; `GET /vendor/cytoscape.min.js` → 200 text/javascript; `GET
   /api/graph` → JSON (200/400); any other path → 404. `db` is closed over from startup, never from the URL.
6. The page (`ui/index.html` + `app.js` + vendored cytoscape) loads with no network: a search box
   queries `/api/graph`; process vs file nodes are visually distinct; each edge shows its rule and
   confidence. (Visual correctness verified manually — stated, not faked.)
7. `cg ui --db <path> [--host 127.0.0.1] [--port 0]` launches the server and prints the bound URL.
8. All prior behavior unchanged; `make test` green including the new API tests.

## Test plan

| Dimension | Proven by | Gate |
|---|---|---|
| correctness | table tests on `graph_payload` using the **`filewatch_db`** fixture for file-cause assertions (only it has file_watch edges) and `graph_db`/`why_db` for ancestry/subtree: pid path (deduped nodes, correct ids/labels incl process `label`+`observed_spawn`, confidence on edges), `q=` path (resolves to expected culprit), id stability/no-duplicate-culprit, min_confidence filtering | must pass |
| reliability | `graph_payload` on empty DB / absent pid / unresolvable question / neither-param / both-params / malformed pid / malformed min_confidence → `{"error":…}`, never raises | must pass |
| concurrency | n/a at the payload layer (fresh graph per request, no shared mutable state). `ThreadingHTTPServer` is a structural choice (avoids one slow query blocking asset loads); not separately load-tested here | n/a |
| scale | binding limit is payload node-count + browser render. Test the cap on BOTH axes: (a) a **near-root pid** (subtree ≈ whole tree) and (b) a **high-fan-out culprit** (many file causes) each return ≤ `max_nodes` nodes with `truncated:true`. (Build itself is the already-measured O(rows), `scale_query.json`.) | report + cap asserted both axes |
| security | server binds 127.0.0.1 only (asserted); NO mutation routes; `db` comes from server config, NOT the query string (client can't point the API at an arbitrary file — asserted); parameterized reads; `pid` int-validated; unknown paths → 404 | must pass |
| regression | headless smoke: `serve` on port 0 in a thread → `GET /` (200 html) + `GET /app.js` (200) + `GET /vendor/cytoscape.min.js` (200) + `GET /api/graph?pid=<fixture>` (200 json) + bad path (404); **teardown: `httpd.shutdown(); thread.join(timeout=2)`; assert not alive**; plus assert the served `index.html`/`app.js` contain no external `http(s)://` script/src outside `/vendor/` (the "no network" half of AC6, which IS testable); existing suite green | must pass |

## Must not regress

- Engine/CLI behavior — `cg tree/path/why` and the whole suite stay green.
- No new runtime dependency (stdlib http.server only; cytoscape is a static vendored asset).

## Evidence plan

- pytest API-contract + headless-smoke output in `make test`.
- `.evidence/artifacts/` — a saved `/api/graph` JSON sample for a fixture pid (before/after readable).
- Manual: a note + (optional) screenshot of the rendered graph — visual look is out of automated scope.

## Rollback

- How: purely additive — new `api/` module, new `ui/` dir, new `cg ui` command, one vendored JS asset.
  Revert the commits. No schema/store/daemon change.
- Migration reversible: yes — n/a (no data or schema change).

## Open risks I'm accepting

- Visual/UX correctness is manually verified (no browser harness) — the automated tests cover the API
  contract and that the page is served, not that it looks right.
- `db` is read fresh per request (no caching) — reuses the O(rows) build cost per query; acceptable for
  a local single-user viewer, revisit with the graph-caching enhancement if it bites.
