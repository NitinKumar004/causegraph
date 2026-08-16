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
        assert _get_status(port, "/api/graph?pid=abc") == 400   # non-int
        assert _get_status(port, "/nope") == 404                # unknown path
    finally:
        httpd.shutdown()
        t.join(timeout=2)
    assert not t.is_alive()  # clean teardown, no leaked thread


def test_served_assets_have_no_external_network_refs():
    """The 'loads with no network' half of AC6, automatically checkable: no http(s)
    script/src references outside same-origin /vendor/."""
    for name in ("index.html", "app.js"):
        with open(os.path.join(UI_DIR, name), encoding="utf-8") as f:
            text = f.read()
        assert "http://" not in text and "https://" not in text, f"{name} references an external URL"
