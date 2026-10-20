"""The runnable examples stay runnable."""

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_hello_local_runs_offline():
    pytest.importorskip("uvicorn")
    pytest.importorskip("starlette")
    done = subprocess.run(
        [sys.executable, str(EXAMPLES / "hello_local.py")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert "card: hello v1.0.0" in out
    assert "send: completed 'hello, world!'" in out
    assert "send: input_required" in out  # the confirmation was not answered for the user
    assert "reply: completed" in out
    assert "event: status completed" in out
    assert "not spec-compliant" not in done.stderr
