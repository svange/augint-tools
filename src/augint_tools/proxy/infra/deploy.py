"""CDK deploy / destroy helpers for the proxy relay stack."""

from __future__ import annotations

import json
import subprocess

from loguru import logger

_STACK_NAME = "ai-tools-proxy-relay"


def _run(cmd: list[str], profile: str | None = None) -> subprocess.CompletedProcess[str]:
    if profile:
        cmd = [*cmd, "--profile", profile]
    return subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603


def deploy_stack(
    profile: str | None = None,
    domain: str | None = None,
    vpc_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Deploy (or update) the proxy relay CDK stack.

    Returns a result dict suitable for CommandResponse.result.
    """
    import tempfile
    from pathlib import Path

    # Write a minimal CDK app to a temp directory
    app_code = _build_cdk_app(domain=domain, vpc_id=vpc_id)

    with tempfile.TemporaryDirectory(prefix="proxy-cdk-") as tmpdir:
        app_file = Path(tmpdir) / "app.py"
        app_file.write_text(app_code)

        cdk_cmd = ["npx", "cdk"]
        if dry_run:
            cdk_cmd.append("synth")
        else:
            cdk_cmd.extend(["deploy", "--require-approval", "never"])

        cdk_cmd.extend(["--app", f"python {app_file}", "--output", f"{tmpdir}/cdk.out"])

        if profile:
            cdk_cmd.extend(["--profile", profile])

        logger.info("Running: {}", " ".join(cdk_cmd))
        result = subprocess.run(cdk_cmd, capture_output=True, text=True, check=True)  # noqa: S603

        output: dict = {
            "stack_name": _STACK_NAME,
            "dry_run": dry_run,
            "cdk_output": result.stdout,
        }

        # After deploy, fetch outputs
        if not dry_run:
            try:
                cf_result = _run(
                    [
                        "aws",
                        "cloudformation",
                        "describe-stacks",
                        "--stack-name",
                        _STACK_NAME,
                        "--output",
                        "json",
                    ],
                    profile,
                )
                stacks = json.loads(cf_result.stdout)
                cf_outputs = {
                    o["OutputKey"]: o["OutputValue"] for o in stacks["Stacks"][0].get("Outputs", [])
                }
                output["outputs"] = cf_outputs
            except (subprocess.CalledProcessError, KeyError, IndexError):
                logger.warning("Could not read stack outputs after deploy")

        return output


def destroy_stack(profile: str | None = None) -> None:
    """Destroy the proxy relay CDK stack."""
    cmd = [
        "npx",
        "cdk",
        "destroy",
        "--force",
        "--app",
        "true",  # placeholder – CDK reads stack name from CF
        _STACK_NAME,
    ]
    if profile:
        cmd.extend(["--profile", profile])

    subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603
    logger.info("Stack {} destroyed", _STACK_NAME)


def _build_cdk_app(domain: str | None = None, vpc_id: str | None = None) -> str:
    """Generate a minimal CDK app.py that imports and instantiates the stack."""
    vpc_arg = f'vpc_id="{vpc_id}"' if vpc_id else ""
    domain_arg = f'domain="{domain}"' if domain else ""
    args = ", ".join(filter(None, [domain_arg, vpc_arg]))

    return f"""\
#!/usr/bin/env python3
import aws_cdk as cdk
from augint_tools.proxy.infra.stack import ProxyRelayStack

app = cdk.App()
ProxyRelayStack(app, "{_STACK_NAME}", {args})
app.synth()
"""
