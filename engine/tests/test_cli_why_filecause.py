"""cg why / cg path against a DB containing FILE nodes (M3f-M4)."""
from causegraph.cli import main


def test_why_shows_file_cause_golden(filewatch_db, capsys):
    rc = main(["why", "why is pid 900 using cpu", "--db", filewatch_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == (
        "/usr/bin/python3 (pid 900) is the likely cause: peak CPU 40.0%.\n"
        "Note: heat/fan is inferred from sustained CPU; no temperature sensor is available.\n"
        "Possibly triggered by a recent change to /etc/app.conf (confidence 0.72).\n"
        "No captured parent (root of the capture window).\n"
    )


def test_path_on_root_with_file_cause_no_crash(filewatch_db, capsys):
    # pid 900 is a root (ppid 1 not captured) with an incoming file_watch edge —
    # cg path must not treat the FILE node as lineage or crash.
    rc = main(["path", "900", "--db", filewatch_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == "900 /usr/bin/python3 (alice)\n"  # no file key in the chain


def test_min_confidence_hides_then_all_shows(filewatch_db, capsys):
    rc = main(["why", "why is pid 900 using cpu", "--db", filewatch_db, "--min-confidence", "0.9"])
    assert rc == 0
    assert "Possibly triggered" not in capsys.readouterr().out  # 0.72 < 0.9

    rc = main(["why", "why is pid 900 using cpu", "--db", filewatch_db, "--all"])
    assert rc == 0
    assert "Possibly triggered by a recent change to /etc/app.conf" in capsys.readouterr().out


def test_why_keyword_on_file_db_does_not_crash(filewatch_db, capsys):
    # AC4b: rank_by must skip FILE keys (mixed (str,str)/(int,int)) — hottest cpu = pid 900.
    rc = main(["why", "why is the fan loud?", "--db", filewatch_db])
    assert rc == 0
    assert "/usr/bin/python3 (pid 900)" in capsys.readouterr().out
