"""AC1: generated Go + Python are never stale vs the schema. Runs the generator's
own --check, which regenerates in-memory and diffs the committed files."""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN = os.path.join(REPO_ROOT, "shared", "schema", "gen.py")


def test_generated_files_not_stale():
    result = subprocess.run(
        [sys.executable, GEN, "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        "generated code is stale — run `make gen`:\n" + result.stderr
    )
