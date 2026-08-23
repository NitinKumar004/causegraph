"""Headless smoke of the HTTP server: routes, content-types, bind, teardown, no-network."""
import json
import os
import threading
import urllib.request

from causegraph.api import server

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_DIR = os.path.join(REPO_ROOT, "ui")


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def _get_status(port, path):
    try:
        return _get(port, path)[0]
    except urllib.error.HTTPError as e:
        return e.code


def test_server_routes_bind_and_teardown(filewatch_db):
    httpd = server.serve(filewatch_db, host="127.0.0.1", port=0)
    host, port = httpd.server_address
    assert host == "127.0.0.1"  # loopback only
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        s, ctype, body = _get(port, "/")
        assert s == 200 and ctype == "text/html" and b"CauseGraph" in body
        assert _get(port, "/app.js")[1] == "text/javascript"
        s, ctype, body = _get(port, "/vendor/cytoscape.min.js")
        assert s == 200 and ctype == "text/javascript" and len(body) > 100_000
        # API
        s, ctype, body = _get(port, "/api/graph?pid=900")
        assert s == 200 and ctype == "application/json"
        data = json.loads(body)
        assert data["culprit"] == "p:900:2000"
        # error paths -> 4xx, never 500
        assert _get_status(port, "/api/graph") == 400          # neither pid nor q
        assert _get_status(port, "/api/graph?pid=abc") == 400   # non-int pid
        assert _get_status(port, "/api/graph?pid=900&min_confidence=hot") == 400  # bad float
        assert _get_status(port, "/api/graph?all=1&max_nodes=lots") == 400  # bad int max_nodes
        assert _get(port, "/api/graph?all=1&max_nodes=1")[0] == 200  # max_nodes honored, not ignored
        assert _get_status(port, "/nope") == 404                # unknown path
        s, ctype, body = _get(port, "/api/meta")                # db name + newest ts for the live/frozen chip
        meta = json.loads(body)
        assert s == 200 and meta["db"].endswith(".db")
        assert isinstance(meta["latest_ts"], int) and meta["latest_ts"] > 0  # fixture has events
    finally:
        httpd.shutdown()
        t.join(timeout=2)
    assert not t.is_alive()  # clean teardown, no leaked thread


def test_bad_db_returns_clean_json_error_not_traceback(tmp_path):
    # db path is a directory -> reading it raises inside graph_payload; the handler
    # must return a clean JSON 500, never a stack-trace.
    bad = str(tmp_path)  # a directory, not a sqlite file
    httpd = server.serve(bad, host="127.0.0.1", port=0)
    _, port = httpd.server_address
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        try:
            status, ctype, body = _get(port, "/api/graph?pid=1")
        except urllib.error.HTTPError as e:
            status, ctype, body = e.code, e.headers.get("Content-Type"), e.read()
        assert status == 500 and ctype == "application/json"
        assert "error" in json.loads(body)
    finally:
        httpd.shutdown()
        t.join(timeout=2)


def test_kill_endpoint_is_guarded(filewatch_db):
    """POST /api/kill needs the anti-CSRF header + a pid; a bogus pid 404s. No real
    process is killed (we use an impossible pid)."""
    import urllib.request
    httpd = server.serve(filewatch_db, host="127.0.0.1", port=0)
    _, port = httpd.server_address
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()

    def post(path, hdr=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=b"", headers=hdr or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r: return r.status
        except urllib.error.HTTPError as e: return e.code

    try:
        assert post("/api/kill?pid=1") == 403                              # missing header
        assert post("/api/kill", {"X-CauseGraph": "1"}) == 400             # missing pid
        assert post("/api/kill?pid=2147480000", {"X-CauseGraph": "1"}) == 404  # no such process
        # pid <= 1 is refused up front: os.kill(-1) signals every reachable process,
        # os.kill(0) the whole process group — never something the UI can intend.
        assert post("/api/kill?pid=-1", {"X-CauseGraph": "1"}) == 400
        assert post("/api/kill?pid=0", {"X-CauseGraph": "1"}) == 400
        assert post("/api/kill?pid=1", {"X-CauseGraph": "1"}) == 400
    finally:
        httpd.shutdown(); t.join(timeout=2)


def test_served_assets_have_no_external_network_refs():
    """The 'loads with no network' half of AC6, automatically checkable: no http(s)
    script/src references outside same-origin /vendor/."""
    for name in ("index.html", "app.js"):
        with open(os.path.join(UI_DIR, name), encoding="utf-8") as f:
            text = f.read()
        assert "http://" not in text and "https://" not in text, f"{name} references an external URL"
