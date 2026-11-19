from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from ai2027_protocol.ai_providers import (
    DeterministicFallbackProvider,
    GeminiFreeProvider,
    HttpJsonResult,
    create_nvidia_nim_provider,
)
from ai2027_protocol.cli import main
from ai2027_protocol.demo import verify_live_providers

_FAKE_GEMINI = "AIza" + "-actions-gemini-test"
_FAKE_NVIDIA = "nvapi" + "-actions-nvidia-test"


class _LiveStubTransport:
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        if urlparse(url).hostname == "generativelanguage.googleapis.com":
            assert headers["x-goog-api-key"] == _FAKE_GEMINI
            return HttpJsonResult(
                200, {"candidates": [{"content": {"parts": [{"text": "pong-gemini"}]}}]}
            )
        assert headers["Authorization"] == f"Bearer {_FAKE_NVIDIA}"
        return HttpJsonResult(200, {"choices": [{"message": {"content": "pong-nvidia"}}]})


def test_cli_demo_audit_bench_route(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert main(["--root", str(repo_root), "demo"]) == 0
    out_demo = capsys.readouterr().out
    assert "Free-First Route" in out_demo
    assert "Python 2027 Protocol Compliance Scorecard" in out_demo

    assert main(["--root", str(repo_root), "demo", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["batch_count"] == 4
    assert payload["otel_spans_captured"] >= 5

    assert main(["--root", str(repo_root), "demo", "--live", "--json"]) == 0
    live_demo = json.loads(capsys.readouterr().out)
    assert "provider_readiness" in live_demo

    assert main(["providers"]) == 0
    assert "GEMINI_API_KEY" in capsys.readouterr().out

    assert main(["providers", "--json"]) == 0
    prov_json = json.loads(capsys.readouterr().out)
    names = [p["provider"] for p in prov_json]
    assert "gemini" in names
    assert "nvidia" in names

    assert main(["live", "--provider", "all", "--json"]) == 0
    live_offline = json.loads(capsys.readouterr().out)
    assert live_offline["routed"]["provider"] == "deterministic"

    assert main(["live", "--provider", "all"]) == 0
    assert "[Live Verify]" in capsys.readouterr().out

    assert main(["live", "--provider", "gemini", "--require-any"]) == 1
    _ = capsys.readouterr()

    assert main(["--root", str(repo_root), "audit", "python-2027", "--json"]) == 0
    audit_json = json.loads(capsys.readouterr().out)
    assert audit_json["projects"][0]["score_pct"] == 100.0

    assert main(["--root", str(repo_root), "audit", "python-2027"]) == 0
    assert "python-2027" in capsys.readouterr().out

    assert main(["bench", "--iterations", "5", "--json"]) == 0
    bench_json = json.loads(capsys.readouterr().out)
    assert len(bench_json["comparisons"]) == 2

    assert main(["bench", "--iterations", "5"]) == 0
    assert "speedup=" in capsys.readouterr().out

    assert main(["route", "Hello CLI", "--offline-only", "--provider", "deterministic"]) == 0
    route_json = json.loads(capsys.readouterr().out)
    assert route_json["provider"] == "deterministic"

    assert (
        main(
            [
                "--root",
                str(repo_root),
                "agent",
                "Inspect python-2027 and propose a Gmail digest",
                "--offline-only",
                "--max-steps",
                "4",
                "--studio-dir",
                str(tmp_path / "cli_studio"),
                "--json",
            ]
        )
        == 0
    )
    agent_json = json.loads(capsys.readouterr().out)
    assert agent_json["status"] == "completed"
    assert len(agent_json["approvals"]) == 1
    aid = agent_json["approvals"][0]

    assert (
        main(
            [
                "--root",
                str(repo_root),
                "approvals",
                "list",
                "--studio-dir",
                str(tmp_path / "cli_studio"),
                "--json",
            ]
        )
        == 0
    )
    ap_list = json.loads(capsys.readouterr().out)
    assert any(item["action_id"] == aid for item in ap_list)

    assert (
        main(
            [
                "--root",
                str(repo_root),
                "approvals",
                "approve",
                aid,
                "--studio-dir",
                str(tmp_path / "cli_studio"),
                "--note",
                "CLI ok",
            ]
        )
        == 0
    )
    approved_out = json.loads(capsys.readouterr().out)
    assert approved_out["status"] == "approved"

    assert (
        main(
            [
                "--root",
                str(repo_root),
                "agent",
                "Build verified HTML deliverable",
                "--offline-only",
                "--max-steps",
                "3",
                "--studio-dir",
                str(tmp_path / "cli_studio_text"),
            ]
        )
        == 0
    )
    assert "[AI-2027 Agent]" in capsys.readouterr().out

    assert (
        main(
            [
                "--root",
                str(repo_root),
                "approvals",
                "list",
                "--studio-dir",
                str(tmp_path / "cli_studio_text"),
            ]
        )
        == 0
    )
    assert "No queued approvals." in capsys.readouterr().out


async def test_verify_live_providers_with_mocked_actions_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "")
    monkeypatch.setenv("NVIDIA_MODEL", "")
    transport = _LiveStubTransport()
    providers = (
        GeminiFreeProvider(api_key=_FAKE_GEMINI, transport=transport),
        create_nvidia_nim_provider(api_key=_FAKE_NVIDIA, transport=transport),
        DeterministicFallbackProvider(),
    )
    report = await verify_live_providers(providers=providers, target="all")
    assert report["configured_count"] == 2
    assert report["live_succeeded"] is True
    assert report["routed"]["provider"] == "gemini"
    assert report["routed"]["simulated"] is False
    serialized = json.dumps(report)
    assert _FAKE_GEMINI not in serialized
    assert _FAKE_NVIDIA not in serialized
