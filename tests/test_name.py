"""The product name lives in agent.NAME and in pyproject.toml; they must agree."""

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from remote_local_sync import NAME, agent, cli
from remote_local_sync.config import CONFIG_DIR

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_pyproject_uses_the_product_name():
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["name"] == NAME
    assert project["scripts"] == {NAME: "remote_local_sync.cli:main"}


def test_names_derive_from_the_product_name():
    assert CONFIG_DIR == "." + NAME
    assert agent.TMP_SUFFIX == "." + NAME + "-tmp"
    assert cli.build_parser().prog == NAME
