import json
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

import aon

ROOT = Path(__file__).resolve().parents[1]


def test_pypi_metadata_and_description_are_safe():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["readme"] == "docs/PYPI.md"
    assert aon.__version__ == project["version"]      # one version, two places

    pypi_readme = (ROOT / project["readme"]).read_text(encoding="utf-8")

    assert not re.search(r"!\[[^\]]*\]\((?!https?://)", pypi_readme)
    assert "```mermaid" not in pypi_readme
    for marker in ("[!IMPORTANT]", "[!TIP]", "[!NOTE]", "[!WARNING]"):
        assert marker not in pypi_readme
    assert "<details" not in pypi_readme.lower()
    # the official MCP Registry verifies ownership of the PyPI package by this marker
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert re.search(r"mcp-name: " + re.escape(server["name"]) + r"(\s|-->)", pypi_readme)
    assert server["version"] == project["version"]
    assert server["packages"][0]["identifier"] == project["name"]
    assert server["packages"][0]["version"] == project["version"]
