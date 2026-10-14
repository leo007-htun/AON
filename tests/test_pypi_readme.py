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

    assert project["version"] == "1.0.1"
    assert project["readme"] == "docs/PYPI.md"
    assert aon.__version__ == "1.0.1"

    pypi_readme = (ROOT / project["readme"]).read_text(encoding="utf-8")

    assert not re.search(r"!\[[^\]]*\]\((?!https?://)", pypi_readme)
    assert "```mermaid" not in pypi_readme
    for marker in ("[!IMPORTANT]", "[!TIP]", "[!NOTE]", "[!WARNING]"):
        assert marker not in pypi_readme
    assert "<details" not in pypi_readme.lower()
