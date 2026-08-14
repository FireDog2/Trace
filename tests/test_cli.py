from pathlib import Path
from types import SimpleNamespace

import pytest

from data_analysis_agent import cli


def test_demo_command_resets_only_the_dedicated_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    demo_dir = tmp_path / ".data-analysis-agent-demo-interview"
    demo_dir.mkdir()
    (demo_dir / "stale.txt").write_text("stale", encoding="utf-8")
    received_data_dirs: list[Path] = []

    def fake_run(settings):
        received_data_dirs.append(settings.data_dir)
        return SimpleNamespace(to_json=lambda: '{"task_status": "completed"}')

    monkeypatch.setattr(cli, "run_portfolio_demo", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "data-analysis-agent",
            "demo",
            "--data-dir",
            str(demo_dir),
            "--reset",
        ],
    )

    cli.main()

    assert received_data_dirs == [demo_dir]
    assert not (demo_dir / "stale.txt").exists()
    assert '"task_status": "completed"' in capsys.readouterr().out


def test_serve_command_forwards_host_port_and_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel_app = object()
    monkeypatch.setattr(cli, "create_app", lambda settings: sentinel_app)
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, host, port: captured.update(app=app, host=host, port=port),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "data-analysis-agent",
            "serve",
            "--data-dir",
            str(tmp_path / "serve-data"),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ],
    )

    cli.main()

    assert captured == {"app": sentinel_app, "host": "0.0.0.0", "port": 9000}


@pytest.mark.parametrize(
    "target,error",
    [
        (Path("..") / ".data-analysis-agent-demo", "inside the current workspace"),
        (Path("unsafe-demo"), "must start with"),
    ],
)
def test_demo_reset_rejects_unsafe_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    error: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match=error):
        cli._validate_demo_reset_target(target.resolve())
