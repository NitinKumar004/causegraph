"""Narrator: deterministic golden output, assumed caveat, single-node path, rss humanize."""
from causegraph.graph.attribution import CPU, RSS
from causegraph.query.narrator import LocalTemplateNarrator

N = LocalTemplateNarrator()


def _path(*pids):
    return [{"pid": p, "exe": f"/bin/p{p}", "user": "u"} for p in pids]


def test_cpu_golden_with_ancestry():
    path = [{"pid": 1, "exe": "/sbin/init", "user": "root"},
            {"pid": 100, "exe": "/usr/bin/python3", "user": "alice"},
            {"pid": 200, "exe": "/usr/bin/ffmpeg", "user": "alice"}]
    out = N.explain(path, path[-1], CPU, 92.5)
    assert out == (
        "/usr/bin/ffmpeg (pid 200) is the likely cause: peak CPU 92.5%.\n"
        "Note: heat/fan is inferred from sustained CPU; no temperature sensor is available.\n"
        "Started by (most recent first):\n"
        "  /usr/bin/python3 (pid 100)\n"
        "  /sbin/init (pid 1)"
    )


def test_rss_humanized_no_cpu_note():
    path = _path(1, 100, 300)
    path[-1]["exe"] = "/usr/bin/chrome"
    out = N.explain(path, path[-1], RSS, 2147483648)
    assert "peak memory 2.0 GiB." in out
    assert "temperature sensor" not in out  # no CPU note for memory


def test_assumed_caveat_present_and_absent():
    path = _path(200)
    with_caveat = N.explain(path, path[-1], CPU, 50.0, assumed=True)
    assert "assuming a CPU/heat issue" in with_caveat
    without = N.explain(path, path[-1], CPU, 50.0, assumed=False)
    assert "assuming a CPU/heat issue" not in without


def test_single_node_path_no_parent():
    path = _path(200)
    out = N.explain(path, path[-1], CPU, 50.0)
    assert "No captured parent (root of the capture window)." in out


def test_deterministic():
    path = _path(1, 2, 3)
    a = N.explain(path, path[-1], CPU, 33.3)
    b = N.explain(path, path[-1], CPU, 33.3)
    assert a == b


def test_causes_line_appended():
    path = _path(900)
    path[-1]["exe"] = "/usr/bin/python3"
    causes = [{"path": "/etc/app.conf", "confidence": 0.72}]
    out = N.explain(path, path[-1], CPU, 40.0, causes=causes)
    assert "Possibly triggered by a recent change to /etc/app.conf (confidence 0.72)." in out


def test_value_none_omits_peak_line():
    path = _path(900)
    path[-1]["exe"] = "/usr/bin/python3"
    causes = [{"path": "/etc/app.conf", "confidence": 0.72}]
    out = N.explain(path, path[-1], CPU, None, causes=causes)
    assert out.startswith("/usr/bin/python3 (pid 900):")
    assert "peak CPU" not in out
    assert "temperature sensor" not in out  # CPU note suppressed when no value
    assert "Possibly triggered by a recent change to /etc/app.conf" in out
