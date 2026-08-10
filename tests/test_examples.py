"""Catch import-time drift in the shipped examples.

The rest of the suite exercises our own modules, so an example importing a
symbol livekit no longer exports passes CI while being broken for everyone who
copies it. Importing each example here fails loudly instead.

Scope: only module-level code runs, so removed or renamed symbols are caught
but the body of ``entrypoint()`` is not. Catching bad keyword arguments to
``AgentSession`` needs a test that actually builds a session.
"""

import importlib.util
import pathlib

import pytest

EXAMPLES = sorted((pathlib.Path(__file__).parent.parent / "examples").glob("*.py"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_imports(path: pathlib.Path) -> None:
    spec = importlib.util.spec_from_file_location(f"_example_{path.stem}", path)
    assert spec and spec.loader
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
