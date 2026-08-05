"""Connect through relay from personal laptop using SSM."""

from __future__ import annotations

import json as _json
import subprocess
import time

import click
from loguru import logger

_STACK_NAME = "ai-tools-proxy-relay"


def _aws_cmd(cmd: list[str], profile: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run an AWS CLI command, appending --profile if set."""
    if profile:
        cmd = [*cmd, "--profile", profile]
    return subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603


def get_instance_id(profile: str | None = None) -> str:
    """Get the relay EC2 instance ID from CloudFormation outputs."""
    result = _aws_cmd(
        [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            _STACK_NAME,
            "--query",
            "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue",
            "--output",
            "text",
        ],
        profile,
    )
    instance_id = result.stdout.strip()
    if not instance_id or instance_id == "None":
        msg = f"No InstanceId output found in stack {_STACK_NAME}"
        raise RuntimeError(msg)
    return instance_id


def get_instance_state(instance_id: str, profile: str | None = None) -> str:
    """Get EC2 instance state (running, stopped, etc.)."""
    result = _aws_cmd(
        [
            "aws",
            "ec2",
            "describe-instances",
            "--instance-ids",
            instance_id,
            "--query",
            "Reservations[0].Instances[0].State.Name",
            "--output",
            "text",
        ],
        profile,
    )
    return result.stdout.strip()


def start_instance(instance_id: str, profile: str | None = None) -> None:
    """Start an EC2 instance."""
    _aws_cmd(["aws", "ec2", "start-instances", "--instance-ids", instance_id], profile)
    logger.info("Start request sent for {}", instance_id)


def wait_for_ssm(instance_id: str, profile: str | None = None, timeout: int = 180) -> bool:
    """Wait for SSM agent to report Online."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        result = _aws_cmd(
            [
                "aws",
                "ssm",
                "describe-instance-information",
                "--filters",
                f"Key=InstanceIds,Values={instance_id}",
                "--query",
                "InstanceInformationList[0].PingStatus",
                "--output",
                "text",
            ],
            profile,
        )
        if result.stdout.strip() == "Online":
            return True
        click.echo(".", nl=False, err=True)
        time.sleep(5)

    msg = f"SSM agent not online after {timeout}s"
    raise TimeoutError(msg)


def establish_connection(
    profile: str | None = None,
    local_port: int = 1080,
    start_instance_flag: bool = True,
    wait_timeout: int = 180,
) -> dict:
    """Establish connection through the relay via SSM port forwarding.

    Returns a dict with connection metadata suitable for CommandResponse.result.
    """
    instance_id = get_instance_id(profile)
    state = get_instance_state(instance_id, profile)

    if state == "stopped" and start_instance_flag:
        click.echo(f"Starting EC2 instance {instance_id}…", err=True)
        start_instance(instance_id, profile)
        state = "pending"

    if state in ("pending", "stopping"):
        click.echo("Waiting for instance to be running…", err=True)
        time.sleep(10)

    click.echo("Waiting for SSM agent…", err=True)
    wait_for_ssm(instance_id, profile, wait_timeout)
    click.echo(" Online!", err=True)

    click.echo(f"\nStarting SSM port forward to localhost:{local_port}", err=True)
    click.echo("Press Ctrl+C to disconnect\n", err=True)

    params = _json.dumps({"portNumber": ["1080"], "localPortNumber": [str(local_port)]})
    cmd = [
        "aws",
        "ssm",
        "start-session",
        "--target",
        instance_id,
        "--document-name",
        "AWS-StartPortForwardingSession",
        "--parameters",
        params,
    ]
    if profile:
        cmd.extend(["--profile", profile])

    subprocess.run(cmd, check=False)  # noqa: S603

    return {
        "instance_id": instance_id,
        "local_port": local_port,
    }
