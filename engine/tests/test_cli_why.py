"""cg why end-to-end against the committed why-fixture (deterministic golden)."""
from causegraph.cli import main


def test_why_fan_is_loud_golden(why_db, capsys):
    rc = main(["why", "why is the fan loud?", "--db", why_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == (
        "/usr/bin/ffmpeg (pid 200) is the likely cause: peak CPU 92.5%.\n"
        "Note: heat/fan is inferred from sustained CPU; no temperature sensor is available.\n"
        "Started by (most recent first):\n"
        "  /usr/bin/python3 (pid 100)\n"
        "  /sbin/init (pid 1)\n"
    )


def test_why_memory_golden(why_db, capsys):
    rc = main(["why", "what is using memory", "--db", why_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == (
        "/usr/bin/chrome (pid 300) is the likely cause: peak memory 2.0 GiB.\n"
        "Started by (most recent first):\n"
        "  /usr/bin/python3 (pid 100)\n"
        "  /sbin/init (pid 1)\n"
    )


def test_why_empty_question_exit1(why_db, capsys):
    assert main(["why", "   ", "--db", why_db]) == 1
    assert "empty" in capsys.readouterr().err.lower()


def test_why_absent_pid_exit1(why_db, capsys):
    assert main(["why", "why is pid 4242 hot", "--db", why_db]) == 1
    assert "4242" in capsys.readouterr().err


def test_why_offvocab_fallback_has_caveat(why_db, capsys):
    rc = main(["why", "what is happening", "--db", why_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert "assuming a CPU/heat issue" in out
    assert "/usr/bin/ffmpeg (pid 200)" in out  # fell back to hottest CPU


def test_why_bad_provider_exit1_no_traceback(why_db, capsys):
    rc = main(["why", "why is the fan loud?", "--db", why_db, "--provider", "bogus"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown narrator provider" in err and "bogus" in err
