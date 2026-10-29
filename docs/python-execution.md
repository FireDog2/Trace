# Python execution

SQL Agent supports read-only SQL plus Python/Pandas analysis of registered CSV,
Parquet and Excel datasets. Its role name and handoff protocol remain unchanged.
Use project-relative paths, declare output files, and pass `input_artifact_ids`
to record dataset lineage. Omitted inputs include project source datasets and
current-task derived datasets; an empty list supplies no datasets.

## Default: Docker

Build the dedicated Linux analysis image before enabling Python analysis:

```powershell
docker build -f docker/python-analysis.Dockerfile -t trace-python:1 .
```

The default `TRACE_PYTHON_BACKEND=docker` never falls back to host execution.
Docker must be available locally with Linux containers; a remote daemon cannot
mount the local staging directory. Missing Docker/image or daemon errors return
a failed tool result. Runtime image pulling and dependency installation are disabled.

Each invocation receives a private snapshot containing only registered dataset
inputs and its generated script, mounted read-only at `/input`. The application
never mounts the real project, credentials, user home, or Docker socket. The
container uses an unprivileged UID, a read-only root filesystem, no capabilities,
no new privileges, no network, and the Docker default seccomp policy.
Its writable `/work` and `/tmp` are size-limited tmpfs mounts. The application
checks exported files, rejects links, changed inputs, missing/undeclared outputs,
and copies only declared outputs into the project. It records both the code and
dataset input IDs as lineage. Host absolute dataset paths must be translated to
the relative paths returned by workspace inspection.

Settings (environment variables are read by `Settings.local`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRACE_PYTHON_IMAGE` | `trace-python:1` | Prebuilt local image; a digest can pin production images |
| `TRACE_PYTHON_TIMEOUT_SECONDS` | `30` | Deadline per execution transport operation |
| `TRACE_PYTHON_MEMORY_MB` | `1024` | Container memory and swap ceiling |
| `TRACE_PYTHON_CPUS` | `1` | CPU quota |
| `TRACE_PYTHON_PIDS_LIMIT` | `64` | Container process/thread limit |
| `TRACE_PYTHON_WORKSPACE_MB` | `256` | `/work` capacity, including input snapshots |
| `TRACE_PYTHON_OUTPUT_LIMIT_BYTES` | `65536` | Maximum retained bytes per stdout/stderr stream |

`/tmp` has a separate 64 MB ceiling. Exceeding log limits stops execution rather
than buffering unlimited output. A timeout or failure triggers forced container
removal, including children. If the Docker daemon becomes unreachable, cleanup
cannot be guaranteed; inspect `trace-python-*` containers after daemon recovery.
An idle container also expires after 30 minutes.

These controls follow [Docker's documented runtime options](https://docs.docker.com/engine/containers/run/).
Containers share a kernel; for hostile multi-tenant workloads use an additional
VM/microVM boundary, a hardened container runtime, and a dedicated execution host.

## Explicit trusted development mode

`TRACE_PYTHON_BACKEND=trusted_local` runs repository-owned/trusted code using the
application interpreter, `-I -B`, a minimal environment, private input copies,
a timeout, bounded log retention, and workspace size/file-count checks.
Tests explicitly select this mode for deterministic code.

**This is not a malicious-code sandbox.** Python AST checks are only a first
filter. Local code can bypass them, access host files and network, allocate host
memory, or create descendants. It has no hard memory/CPU/process/disk boundary.
Do not select it for untrusted generated code. Docker policy tests can run
without Docker; the live integration suite requires `TRACE_TEST_DOCKER=1` and
the prebuilt image.

## Verification (2026-10-29)

Backend: 410 tests passed, 85.71% branch-aware coverage. Three integration tests
were skipped: one PostgreSQL test without `TEST_POSTGRES_DSN`, and two Docker
tests without `TRACE_TEST_DOCKER=1` (Docker is unavailable in this development
environment). Frontend: 49 tests and production build passed. Ruff, formatting,
and mypy checks passed. CSV/Excel-to-Pandas-to-validated-handoff tests use real
Pandas execution with deterministic model providers in explicit trusted-local
mode. Docker policy/cleanup tests use mocked transport; live container resource
and isolation behavior still requires verification on a Docker-equipped host.
