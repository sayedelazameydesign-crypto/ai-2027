from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


def test_colab_notebook_valid_nbformat_ast_and_secret_hygiene(
    capsys: pytest.CaptureFixture[str],
) -> None:
    nb_path = Path(__file__).resolve().parents[1] / "colab" / "ai2027_python_protocol_lab.ipynb"
    assert nb_path.is_file()
    data = json.loads(nb_path.read_text(encoding="utf-8"))
    assert data["nbformat"] == 4
    assert len(data["cells"]) >= 4

    code_cells = [c for c in data["cells"] if c.get("cell_type") == "code"]
    assert len(code_cells) >= 4
    namespace: dict[str, object] = {}
    for idx, cell in enumerate(code_cells):
        src = "".join(cell.get("source", []))
        assert src.strip(), f"Code cell {idx} must not be empty"
        ast.parse(src, filename=f"colab_cell_{idx}.py")
        assert "AIza" not in src
        assert "sk-" not in src
        assert "ghp_" not in src
        if cell.get("id") != "setup-uv-repo":
            exec(compile(src, f"colab_cell_{idx}.py", "exec"), namespace)  # noqa: S102

    output = capsys.readouterr().out
    assert "Selected provider:" in output
    assert "Python 2027 Protocol Compliance Scorecard" in output
