from app.tools.mmx_cli import MmxCliToolProvider
from app.main import create_app
from fastapi.testclient import TestClient


def test_mmx_cli_provider_reports_missing_binary():
    provider = MmxCliToolProvider(executable="definitely-not-installed-mmx")

    result = provider.auth_status()

    assert result.ok is False
    assert result.command == ["definitely-not-installed-mmx", "auth", "status"]
    assert "not installed" in result.summary


def test_mmx_cli_provider_runs_quota_command_with_injected_runner():
    calls = []

    def runner(command, timeout_seconds):
        calls.append((command, timeout_seconds))
        return 0, "quota ok", ""

    provider = MmxCliToolProvider(executable="mmx", runner=runner)

    result = provider.quota()

    assert result.ok is True
    assert result.command == ["mmx", "quota"]
    assert result.stdout == "quota ok"
    assert result.summary == "mmx quota completed"
    assert calls == [(["mmx", "quota"], 30)]


def test_mmx_status_endpoint_reports_tool_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    client = TestClient(create_app())

    response = client.get("/tools/mmx/status")

    assert response.status_code == 200
    body = response.json()
    assert "auth" in body
    assert "quota" in body
    assert "command" in body["auth"]
