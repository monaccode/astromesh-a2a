import importlib.metadata
import subprocess
import sys
import textwrap

import astromesh_a2a


def test_import():
    assert astromesh_a2a.__version__ == importlib.metadata.version("astromesh-a2a")


def test_server_names_listed_but_not_star_exported():
    for name in ("create_app", "serve", "api_key_auth"):
        assert name not in astromesh_a2a.__all__
        assert name in dir(astromesh_a2a)
    assert "AgentClosedError" in astromesh_a2a.__all__
    assert not hasattr(astromesh_a2a, "ConfirmationRequired")
    assert issubclass(astromesh_a2a.AgentClosedError, RuntimeError)


def test_star_import_works_without_the_server_extra():
    """A base install (no starlette/uvicorn): `import *` must not touch the server."""
    code = textwrap.dedent(
        """
        import importlib.abc, sys

        class NoServerExtra(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in ("starlette", "uvicorn", "sse_starlette"):
                    raise ImportError(f"No module named {name!r} (simulated base install)")
                return None

        sys.meta_path.insert(0, NoServerExtra())
        ns = {}
        exec("from astromesh_a2a import *", ns)
        assert "RemoteAgent" in ns and "create_app" not in ns
        import astromesh_a2a
        try:
            astromesh_a2a.create_app
        except ImportError as exc:
            assert "server" in str(exc)
        else:
            raise AssertionError("create_app imported without the server extra")
        print("ok")
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
