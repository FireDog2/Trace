"""Bounded execution transports; trusted_local is deliberately not a security sandbox."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from data_analysis_agent.settings import Settings


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    timed_out: bool = False


def minimal_environment() -> dict[str, str]:
    # Never inherit provider keys, database credentials, PYTHONPATH or user site settings.
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    for name in ("SystemRoot", "WINDIR"):
        if value := os.environ.get(name):
            environment[name] = value
    return environment


def docker_environment() -> dict[str, str]:
    # Client connection settings stay on the host; docker run forwards none of them.
    environment = minimal_environment()
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_RUNTIME_DIR",
        "DOCKER_CONFIG",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        if value := os.environ.get(name):
            environment[name] = value
    return environment


def bounded_process(
    command: list[str], *, cwd: Path, settings: Settings, environment: dict[str, str]
) -> ProcessResult:
    """Drain both pipes while retaining bounded bytes; kill on timeout/log/workspace limits."""
    exceeded = Event()
    buffers = [bytearray(), bytearray()]

    def drain(stream, target: bytearray) -> None:
        try:
            while chunk := stream.read(4096):
                remaining = settings.python_output_limit_bytes - len(target)
                target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    exceeded.set()
        finally:
            stream.close()

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return ProcessResult(None, error="Python execution backend is unavailable")
    threads = [
        Thread(target=drain, args=(stream, buffer), daemon=True)
        for stream, buffer in zip((process.stdout, process.stderr), buffers, strict=True)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + settings.python_timeout_seconds
    error = ""
    timed_out = False
    while process.poll() is None:
        if exceeded.is_set():
            error = "Python output exceeded its byte limit"
        elif time.monotonic() >= deadline:
            error = "Python execution exceeded its time limit"
            timed_out = True
        elif settings.python_backend == "trusted_local" and workspace_exceeded(cwd, settings):
            error = "Python workspace exceeded its resource limit"
        if error:
            process.kill()
            break
        time.sleep(0.02)
    process.wait()
    for thread in threads:
        thread.join(timeout=2)
    if exceeded.is_set() and not error:
        error = "Python output exceeded its byte limit"
    return ProcessResult(
        process.returncode,
        buffers[0].decode("utf-8", errors="replace"),
        buffers[1].decode("utf-8", errors="replace"),
        error,
        timed_out,
    )


def workspace_exceeded(root: Path, settings: Settings) -> bool:
    size = 0
    count = 0
    for path in root.rglob("*"):
        count += 1
        if path.is_symlink() or path.is_junction():
            return True
        if path.is_file():
            try:
                size += path.stat().st_size
            except FileNotFoundError:  # A process may replace its temporary files.
                continue
        if count > 4096 or size > settings.python_workspace_mb * 1024 * 1024:
            return True
    return False


_BOOTSTRAP = """import os, runpy, shutil
from pathlib import Path
for item in Path('/input').iterdir():
    destination = Path('/work') / item.name
    if item.is_dir():
        shutil.copytree(item, destination)
    else:
        shutil.copyfile(item, destination)
os.chdir('/work')
os.environ['HOME'] = '/tmp'
os.environ['MPLCONFIGDIR'] = '/tmp/matplotlib'
runpy.run_path(os.environ['TRACE_SCRIPT'], run_name='__main__')
"""


def docker_command(
    settings: Settings, docker: str, name: str, stage: Path, script: str
) -> list[str]:
    # Only the per-execution staging snapshot is mounted; never the real project or Docker socket.
    return [
        docker,
        "run",
        "--detach",
        "--name",
        name,
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--memory",
        f"{settings.python_memory_mb}m",
        "--memory-swap",
        f"{settings.python_memory_mb}m",
        "--cpus",
        str(settings.python_cpus),
        "--pids-limit",
        str(settings.python_pids_limit),
        "--ulimit",
        "nofile=256:256",
        "--ulimit",
        "core=0:0",
        "--tmpfs",
        f"/work:rw,nosuid,nodev,noexec,size={settings.python_workspace_mb}m,mode=1777",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--mount",
        f"type=bind,source={stage},target=/input,readonly",
        "--workdir=/work",
        "--log-driver=none",
        "--env",
        f"TRACE_SCRIPT={script}",
        "--env",
        "OMP_NUM_THREADS=1",
        "--env",
        "OPENBLAS_NUM_THREADS=1",
        "--env",
        "MKL_NUM_THREADS=1",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint=python",
        settings.python_image,
        "-I",
        "-B",
        "-c",
        "import time; time.sleep(1800)",
    ]


def run_in_workspace(settings: Settings, stage: Path, script: str, exported: Path) -> ProcessResult:
    if settings.python_backend == "trusted_local":
        # Trusted development only: -I and a copied cwd do not contain malicious Python.
        temporary_home = stage.parent / "runtime-home"
        temporary_home.mkdir(exist_ok=True)
        environment = minimal_environment()
        environment.update(
            {
                name: str(temporary_home)
                for name in ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "MPLCONFIGDIR")
            }
        )
        result = bounded_process(
            [sys.executable, "-I", "-B", script],
            cwd=stage,
            settings=settings,
            environment=environment,
        )
        if not workspace_exceeded(stage, settings):
            shutil.copytree(stage, exported, dirs_exist_ok=True)
        else:
            return ProcessResult(
                result.returncode, error="Python workspace exceeded its resource limit"
            )
        return result
    docker = shutil.which("docker")
    if docker is None:
        return ProcessResult(None, error="Docker Python backend is unavailable; no local fallback")
    name = f"trace-python-{uuid4().hex}"
    try:
        created = bounded_process(
            docker_command(settings, docker, name, stage, script),
            cwd=stage,
            settings=settings,
            environment=docker_environment(),
        )
        if created.returncode != 0 or created.error:
            return ProcessResult(
                None, error="Docker Python backend could not start; no local fallback"
            )
        result = bounded_process(
            [docker, "exec", name, "python", "-I", "-B", "-c", _BOOTSTRAP],
            cwd=stage,
            settings=settings,
            environment=docker_environment(),
        )
        if result.returncode != 0 or result.error:
            # Keep Python tracebacks for analysis repair, suppress Docker daemon diagnostics.
            stderr = result.stderr if "Traceback (most recent call last):" in result.stderr else ""
            return ProcessResult(
                result.returncode,
                result.stdout,
                stderr,
                result.error or "Python container failed",
                result.timed_out,
            )
        # /work is a size-bounded tmpfs. Copy into a private directory before validating outputs.
        copied = bounded_process(
            [docker, "cp", f"{name}:/work/.", str(exported)],
            cwd=stage,
            settings=settings,
            environment=docker_environment(),
        )
        if copied.returncode != 0 or copied.error or workspace_exceeded(exported, settings):
            return ProcessResult(
                None, error="Python container outputs could not be collected safely"
            )
        return result
    finally:
        # Killing the Docker client alone does not stop the container.
        try:
            subprocess.run(
                [docker, "rm", "--force", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                env=docker_environment(),
            )
        except (OSError, subprocess.TimeoutExpired):
            # No host execution fallback, even if the daemon has become unavailable.
            pass
