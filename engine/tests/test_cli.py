"""CLI contract: help text (regression row) and pid arg validation (security row)."""
import pytest

from causegraph.cli import main


def test_help_lists_m2_subcommands(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    for token in ("cg", "tree", "path", "ui", "load"):
        assert token in out, f"help missing {token!r}"


def test_no_why_subcommand(capsys):
    """The natural-language `cg why` was removed — invoking it is an argparse error."""
    with pytest.raises(SystemExit) as ei:
        main(["why", "why is the fan loud?", "--db", "ignored.db"])
    assert ei.value.code == 2  # invalid choice
    assert "invalid choice: 'why'" in capsys.readouterr().err


def test_rejects_non_integer_pid(capsys):
    """Security row: the pid arg is validated as int; a non-int is rejected, not
    passed into any query."""
    with pytest.raises(SystemExit) as ei:
        main(["tree", "not-a-pid", "--db", "ignored.db"])
    assert ei.value.code == 2  # argparse usage error
    assert "invalid int value" in capsys.readouterr().err


def test_absent_pid_path_returns_one(graph_db):
    """A valid but absent pid returns 1 from `cg path`, never crashes."""
    assert main(["path", "88888", "--db", graph_db]) == 1
