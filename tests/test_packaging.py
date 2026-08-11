from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from livekit.plugins import reson8

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_PACKAGING_TESTS"),
    reason="set RUN_PACKAGING_TESTS=1 to run (slow: builds and installs the wheel)",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Runs inside the throwaway venv. Reports what the *installed* package looks like, so the
# assertions can tell it apart from the source tree sitting in the repo.
_SMOKE = """
import json, sys
from livekit.plugins import reson8
print(json.dumps({
    "version": reson8.__version__,
    "missing": [n for n in sys.argv[1:] if not hasattr(reson8, n)],
    "path": reson8.__file__,
}))
"""


def _run(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    argv = [str(a) for a in args]
    result = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, check=False)
    if result.returncode != 0:
        pytest.fail(
            f"command failed: {' '.join(argv)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to build and install the wheel")
    out_dir = tmp_path_factory.mktemp("wheel")
    _run("uv", "build", "--wheel", "--out-dir", out_dir, cwd=_PROJECT_ROOT)
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


def test_wheel_installs_and_imports(wheel: Path, tmp_path: Path) -> None:
    """Install the built wheel into a clean venv and import it from there.

    Every other test imports from the source tree, so none of them can see a packaging
    fault: a module missing from the wheel, a runtime dependency that is imported but
    not declared, or an __init__ that only works because the repo is on sys.path.
    """
    venv_dir = tmp_path / "venv"
    _run("uv", "venv", "--python", sys.executable, venv_dir)
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    python = venv_dir / bin_dir / ("python.exe" if sys.platform == "win32" else "python")
    _run("uv", "pip", "install", "--python", python, wheel)

    # cwd is the temp dir, so the import can only resolve to the venv, never to ./livekit.
    result = _run(python, "-c", _SMOKE, *reson8.__all__, cwd=tmp_path)
    installed = json.loads(result.stdout)
    installed_path = Path(installed["path"])

    assert installed_path.is_relative_to(venv_dir), (
        f"imported the source tree rather than the installed wheel: {installed_path}"
    )
    assert installed["version"] == reson8.__version__
    assert not installed["missing"], f"public names absent from the wheel: {installed['missing']}"
    assert (installed_path.parent / "py.typed").exists(), "the wheel does not ship py.typed"
