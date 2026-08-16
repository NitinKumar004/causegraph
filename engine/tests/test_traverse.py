"""Traversal + CLI output determinism against the committed fixture (AC7)."""
from causegraph.cli import main
from causegraph.graph import builder, traverse


def test_tree_and_path_structure(graph_events):
    g = builder.build(graph_events)

    bash = traverse.latest_instance(g, 100)
    assert bash == (100, 200)

    # tree(100): descendants sorted deterministically
    tree = [k for _, k in traverse.subtree(g, bash)]
    assert tree == [(100, 200), (200, 300), (201, 350), (300, 360)]

    # path(300): root-ward init -> bash -> vim -> grep
    grep = traverse.latest_instance(g, 300)
    path = traverse.ancestry_path(g, grep)
    assert [k[0] for k in path] == [1, 100, 201, 300]


def test_cli_tree_output(graph_db, capsys):
    rc = main(["tree", "100", "--db", graph_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == (
        "100 /bin/bash (alice)\n"
        "  200 /bin/sleep (alice)\n"
        "  201 /usr/bin/vim (alice)\n"
        "    300 /usr/bin/grep (alice)\n"
    )


def test_cli_path_output(graph_db, capsys):
    rc = main(["path", "300", "--db", graph_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == (
        "1 /sbin/init (root) ~\n"
        "  100 /bin/bash (alice)\n"
        "    201 /usr/bin/vim (alice)\n"
        "      300 /usr/bin/grep (alice)\n"
    )


def test_cli_unknown_pid(graph_db, capsys):
    rc = main(["tree", "99999", "--db", graph_db])
    assert rc == 1
    assert "no process with pid 99999" in capsys.readouterr().err
