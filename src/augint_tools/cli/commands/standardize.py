"""Top-level ``ai-tools standardize`` command.

Thin wrapper around ``ai-shell standardize``. Three modes:

- ``--verify``     delegates to ``ai-shell standardize repo --verify --json``
- ``--area X``    delegates to ``ai-shell standardize X [--validate] <path>``
- ``--all``       delegates to ``ai-shell standardize repo --all [--dry-run]``

PATH defaults to the current working directory. The process never ``cd``s
into PATH — it passes the path as an argument so the parent's resolved
``augint-shell`` version stays in effect (avoids the uv shared-venv
downgrade trap).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from augint_tools.output import CommandResponse, emit_response

_VALID_AREAS = ("pipeline", "precommit", "renovate", "release", "dotfiles")

# Subprocess timeout. Standardize --all can write several config files and
# touch the filesystem; 5 minutes is generous without being silly.
_TIMEOUT_SECS = 300

# Exit codes for standardize:
#   0 clean
#   1 drift (or any non-zero from ai-shell that isn't a launch failure)
#   2 error (ai-shell couldn't run, unparseable output, bad path)
_EXIT_OK = 0
_EXIT_DRIFT = 1
_EXIT_ERROR = 2


def _get_output_opts(ctx: click.Context) -> dict[str, Any]:
    obj = ctx.obj or {}
    return {
        "json_mode": obj.get("json_mode", False),
        "actionable": obj.get("actionable", False),
        "summary_only": obj.get("summary_only", False),
    }


def _run_ai_shell(cmd: list[str]) -> tuple[int, str, str]:
    """Invoke ai-shell, capturing stdout/stderr.

    Returns ``(exit_code, stdout, stderr)``. ``exit_code == -1`` means the
    subprocess could not be launched (binary missing or timed out) and
    ``stderr`` carries the reason.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECS,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", "ai-shell executable not found on PATH"
    except subprocess.TimeoutExpired:
        return -1, "", f"ai-shell timed out after {_TIMEOUT_SECS}s"


def _echo_captured(stdout: str, stderr: str, opts: dict[str, Any]) -> None:
    """Forward captured subprocess output to the terminal in human mode.

    Tests assert against the envelope, not this echo, so it runs only in
    interactive (non-JSON, non-summary) mode.
    """
    if opts.get("json_mode") or opts.get("summary_only"):
        return
    if stdout:
        click.echo(stdout.rstrip())
    if stderr:
        click.echo(stderr.rstrip(), err=True)


@click.command("standardize")
@click.argument(
    "path",
    required=False,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
)
@click.option("--verify", is_flag=True, default=False, help="Read-only drift report.")
@click.option(
    "--area",
    type=click.Choice(_VALID_AREAS),
    default=None,
    help="Run a single standardization step.",
)
@click.option(
    "--all",
    "run_all",
    is_flag=True,
    default=False,
    help="Run the full standardization sequence.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Compute what would change without writing (only with --all or --area dotfiles).",
)
@click.pass_context
def standardize(
    ctx: click.Context,
    path: Path | None,
    verify: bool,
    area: str | None,
    run_all: bool,
    dry_run: bool,
) -> None:
    """Run or verify repository standardization.

    Thin wrapper around ``ai-shell standardize``. PATH defaults to cwd.
    Specify exactly one of ``--verify``, ``--area <name>``, or ``--all``.
    """
    opts = _get_output_opts(ctx)
    target = (path or Path.cwd()).resolve()

    if not target.exists():
        emit_response(
            CommandResponse.error(
                "standardize",
                "repo",
                f"Path does not exist: {target}",
            ),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    has_area = area is not None

    # Mode validation. Valid combinations:
    #   --verify                       -> full-repo read-only verify
    #   --verify --area pipeline       -> T10-2: pipeline --validate (already read-only)
    #   --area <name>                  -> single-step, may write
    #   --area dotfiles [--dry-run]    -> dotfiles supports dry-run upstream
    #   --all [--dry-run]              -> full sequence
    if not verify and not has_area and not run_all:
        emit_response(
            CommandResponse.error(
                "standardize",
                "repo",
                "Must specify one of --verify, --area <name>, or --all.",
            ),
            **opts,
        )
        sys.exit(_EXIT_DRIFT)

    if run_all and (verify or has_area):
        emit_response(
            CommandResponse.error(
                "standardize",
                "repo",
                "--all is mutually exclusive with --verify and --area.",
            ),
            **opts,
        )
        sys.exit(_EXIT_DRIFT)

    if verify and has_area and area != "pipeline":
        emit_response(
            CommandResponse.error(
                "standardize",
                "repo",
                f"--verify can only be combined with --area pipeline "
                f"(which is already read-only). --area {area} has no verify mode.",
            ),
            **opts,
        )
        sys.exit(_EXIT_DRIFT)

    if verify and dry_run:
        emit_response(
            CommandResponse.error(
                "standardize",
                "repo",
                "--dry-run cannot be combined with --verify (verify is already read-only).",
            ),
            **opts,
        )
        sys.exit(_EXIT_DRIFT)

    # Dispatch. `--area` takes precedence when set; a bare `--verify`
    # runs the full-repo verifier.
    if area is not None:
        _run_area(target, area, dry_run, opts)
    elif verify:
        _run_verify(target, opts)
    else:
        _run_all(target, dry_run, opts)


def _run_verify(path: Path, opts: dict[str, Any]) -> None:
    cmd = ["ai-shell", "standardize", "repo", "--verify", "--json", str(path)]
    rc, stdout, stderr = _run_ai_shell(cmd)

    if rc == -1:
        detail = stderr.strip() or "ai-shell failed to launch"
        emit_response(
            CommandResponse.error("standardize --verify", "repo", detail),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    if not stdout.strip():
        detail = stderr.strip() or "ai-shell produced no output"
        emit_response(
            CommandResponse(
                command="standardize --verify",
                scope="repo",
                status="error",
                summary=detail,
                errors=[detail],
                result={"path": str(path), "stderr": stderr},
            ),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    try:
        parsed: dict[str, Any] = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = f"Failed to parse ai-shell JSON: {exc}"
        emit_response(
            CommandResponse(
                command="standardize --verify",
                scope="repo",
                status="error",
                summary=detail,
                errors=[detail],
                result={"path": str(path), "stdout": stdout, "stderr": stderr},
            ),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    findings = parsed.get("findings") if isinstance(parsed, dict) else None
    if not isinstance(findings, list):
        detail = "ai-shell JSON missing 'findings' list"
        emit_response(
            CommandResponse(
                command="standardize --verify",
                scope="repo",
                status="error",
                summary=detail,
                errors=[detail],
                result={"path": str(path), "raw": parsed},
            ),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    pass_count = sum(1 for f in findings if f.get("status") == "PASS")
    drift_count = sum(1 for f in findings if f.get("status") == "DRIFT")
    fail_count = sum(1 for f in findings if f.get("status") == "FAIL")

    # T10-1: status MUST derive from the finding counts, not from the
    # `overall` string or ai-shell's exit code. Historically we checked
    # `overall == "pass"` but ai-shell emits `"clean"` (and `rc` can be
    # non-zero even for clean repos when a venv downgrade warning leaks).
    # The counts are the single source of truth.
    if fail_count > 0 or drift_count > 0:
        status = "drift"
        exit_code = _EXIT_DRIFT
    elif pass_count > 0:
        status = "ok"
        exit_code = _EXIT_OK
    else:
        # Parseable JSON with zero findings — ai-shell didn't tell us
        # anything useful. Treat it as an error rather than silently
        # claiming the repo is clean.
        detail = "ai-shell returned zero findings"
        emit_response(
            CommandResponse(
                command="standardize --verify",
                scope="repo",
                status="error",
                summary=detail,
                errors=[detail],
                result=parsed,
            ),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    summary = f"{path.name}: {pass_count} pass, {drift_count} drift, {fail_count} fail"
    next_actions: list[str] = []
    if drift_count or fail_count:
        next_actions.append("run /ai-standardize-repo skill to fix drift")

    emit_response(
        CommandResponse(
            command="standardize --verify",
            scope="repo",
            status=status,
            summary=summary,
            result=parsed,
            next_actions=next_actions,
        ),
        **opts,
    )
    sys.exit(exit_code)


def _run_area(path: Path, area: str, dry_run: bool, opts: dict[str, Any]) -> None:
    # Build ai-shell invocation per area.
    if area == "pipeline":
        if dry_run:
            emit_response(
                CommandResponse.error(
                    "standardize --area pipeline",
                    "repo",
                    "--dry-run is not supported with --area pipeline (validate is already read-only).",
                ),
                **opts,
            )
            sys.exit(_EXIT_DRIFT)
        cmd = ["ai-shell", "standardize", "pipeline", "--validate"]
        if opts.get("json_mode"):
            cmd.append("--json")
        cmd.append(str(path))
    elif area == "dotfiles":
        cmd = ["ai-shell", "standardize", "dotfiles"]
        if dry_run:
            cmd.append("--dry-run")
        cmd.append(str(path))
    else:
        # precommit, renovate, release — no --dry-run support upstream.
        if dry_run:
            emit_response(
                CommandResponse.error(
                    f"standardize --area {area}",
                    "repo",
                    f"--dry-run is not supported with --area {area}.",
                ),
                **opts,
            )
            sys.exit(_EXIT_DRIFT)
        cmd = ["ai-shell", "standardize", area, str(path)]

    rc, stdout, stderr = _run_ai_shell(cmd)

    if rc == -1:
        detail = stderr.strip() or "ai-shell failed to launch"
        emit_response(
            CommandResponse.error(f"standardize --area {area}", "repo", detail),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    _echo_captured(stdout, stderr, opts)

    result: dict[str, Any] = {
        "path": str(path),
        "area": area,
        "exit_code": rc,
    }
    # Surface ai-shell stdout for JSON consumers. Try to parse if pipeline
    # gave us JSON, otherwise carry it as a string.
    if stdout.strip():
        if area == "pipeline" and opts.get("json_mode"):
            try:
                result["pipeline"] = json.loads(stdout)
            except json.JSONDecodeError:
                result["stdout"] = stdout
        else:
            result["stdout"] = stdout
    if stderr.strip():
        result["stderr"] = stderr

    if rc == 0:
        status = "ok"
        exit_code = _EXIT_OK
        summary = f"ai-shell standardize {area} completed"
    else:
        # ai-shell returned non-zero. For pipeline --validate this means
        # drift; for write commands it means the subcommand failed.
        status = "drift" if area == "pipeline" else "error"
        exit_code = _EXIT_DRIFT if area == "pipeline" else _EXIT_ERROR
        summary = f"ai-shell standardize {area} exited {rc}"

    errors = [stderr.strip()] if rc != 0 and stderr.strip() else []
    emit_response(
        CommandResponse(
            command=f"standardize --area {area}",
            scope="repo",
            status=status,
            summary=summary,
            result=result,
            errors=errors,
        ),
        **opts,
    )
    sys.exit(exit_code)


def _run_all(path: Path, dry_run: bool, opts: dict[str, Any]) -> None:
    cmd = ["ai-shell", "standardize", "repo", "--all"]
    if dry_run:
        cmd.append("--dry-run")
        # --json is only valid on `repo` with --verify or --all --dry-run.
        if opts.get("json_mode"):
            cmd.append("--json")
    cmd.append(str(path))

    rc, stdout, stderr = _run_ai_shell(cmd)

    if rc == -1:
        detail = stderr.strip() or "ai-shell failed to launch"
        emit_response(
            CommandResponse.error(
                "standardize --all" + (" --dry-run" if dry_run else ""),
                "repo",
                detail,
            ),
            **opts,
        )
        sys.exit(_EXIT_ERROR)

    _echo_captured(stdout, stderr, opts)

    result: dict[str, Any] = {
        "path": str(path),
        "dry_run": dry_run,
        "exit_code": rc,
    }
    if dry_run and opts.get("json_mode") and stdout.strip():
        try:
            result["plan"] = json.loads(stdout)
        except json.JSONDecodeError:
            result["stdout"] = stdout
    else:
        if stdout.strip():
            result["stdout"] = stdout
    if stderr.strip():
        result["stderr"] = stderr

    status = "ok" if rc == 0 else "error"
    exit_code = _EXIT_OK if rc == 0 else _EXIT_ERROR
    mode_str = "dry-run" if dry_run else "apply"
    summary = (
        f"ai-shell standardize --all ({mode_str}) completed"
        if rc == 0
        else f"ai-shell standardize --all ({mode_str}) exited {rc}"
    )
    errors = [stderr.strip()] if rc != 0 and stderr.strip() else []

    emit_response(
        CommandResponse(
            command="standardize --all" + (" --dry-run" if dry_run else ""),
            scope="repo",
            status=status,
            summary=summary,
            result=result,
            errors=errors,
        ),
        **opts,
    )
    sys.exit(exit_code)
