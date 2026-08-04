"""Query relay infrastructure status."""

from __future__ import annotations

import subprocess

from loguru import logger

_STACK_NAME = "ai-tools-proxy-relay"


def _aws_cmd(cmd: list[str], profile: str | None = None) -> subprocess.CompletedProcess[str]:
    if profile:
        cmd = [*cmd, "--profile", profile]
    return subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603


def get_status(profile: str | None = None) -> dict:
    """Gather relay status from AWS.

    Returns a dict suitable for CommandResponse.result.
    """
    # Check if stack exists
    try:
        result = _aws_cmd(
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
    except subprocess.CalledProcessError:
        return {
            "stack_exists": False,
            "summary": "Relay infrastructure not deployed",
        }

    import json

    stacks = json.loads(result.stdout)
    stack = stacks["Stacks"][0]
    stack_status = stack.get("StackStatus", "UNKNOWN")
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    instance_id = outputs.get("InstanceId")

    if not instance_id:
        return {
            "stack_exists": True,
            "stack_status": stack_status,
            "summary": f"Stack {stack_status} but no instance ID in outputs",
        }

    # Get instance details
    try:
        inst_result = _aws_cmd(
            [
                "aws",
                "ec2",
                "describe-instances",
                "--instance-ids",
                instance_id,
                "--output",
                "json",
            ],
            profile,
        )
        inst_data = json.loads(inst_result.stdout)
        instance = inst_data["Reservations"][0]["Instances"][0]
        ec2_state = instance["State"]["Name"]
        launch_time = instance.get("LaunchTime", "")
    except (subprocess.CalledProcessError, KeyError, IndexError):
        ec2_state = "unknown"
        launch_time = ""

    # Check SSM status
    ssm_status = "Unknown"
    try:
        ssm_result = _aws_cmd(
            [
                "aws",
                "ssm",
                "describe-instance-information",
                "--filters",
                f"Key=InstanceIds,Values={instance_id}",
                "--output",
                "json",
            ],
            profile,
        )
        ssm_data = json.loads(ssm_result.stdout)
        info_list = ssm_data.get("InstanceInformationList", [])
        if info_list:
            ssm_status = info_list[0].get("PingStatus", "Unknown")
    except subprocess.CalledProcessError:
        logger.debug("Could not query SSM status")

    # Compute uptime
    uptime_minutes = None
    if launch_time and ec2_state == "running":
        import datetime as _dt

        try:
            lt = _dt.datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            uptime_minutes = int((_dt.datetime.now(_dt.UTC) - lt).total_seconds() / 60)
        except (ValueError, TypeError):
            pass

    # Build summary
    parts = [f"EC2 {ec2_state}"]
    if ssm_status == "Online":
        parts.append("SSM online")
    if uptime_minutes is not None:
        parts.append(f"up {uptime_minutes}m")

    return {
        "stack_exists": True,
        "stack_status": stack_status,
        "ec2_state": ec2_state,
        "ec2_instance_id": instance_id,
        "ssm_status": ssm_status,
        "uptime_minutes": uptime_minutes,
        "cost_estimate_hourly": "$0.004",
        "summary": ", ".join(parts),
    }
