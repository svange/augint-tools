"""CLI commands for on-demand EC2 relay proxy."""

import sys

import click

from augint_tools.output import CommandResponse, emit_response


def _get_output_opts(ctx: click.Context) -> dict:
    obj = ctx.obj or {}
    return {"json_mode": obj.get("json_mode", False)}


@click.group("proxy")
@click.option("--profile", default=None, help="AWS profile to use.")
@click.pass_context
def proxy_group(ctx, profile):
    """On-demand EC2 relay for remote VPN access."""
    ctx.ensure_object(dict)
    ctx.obj["aws_profile"] = profile


@proxy_group.command("serve")
@click.option("--relay-host", required=True, help="Relay hostname (from infra deploy).")
@click.option("--relay-port", default=2222, show_default=True, help="SSH port on relay.")
@click.option("--remote-port", default=1080, show_default=True, help="Port to expose on relay.")
@click.option("--local-port", default=1080, show_default=True, help="Local SOCKS proxy port.")
@click.option("--key-file", default=None, help="SSH private key for relay auth.")
@click.pass_context
def serve_cmd(ctx, relay_host, relay_port, remote_port, local_port, key_file):
    """Run on work computer: start SOCKS proxy and reverse tunnel.

    This command starts a local SOCKS5 proxy, connects to the relay via
    SSH, and creates a reverse tunnel so the relay forwards traffic through
    the work computer's network (including VPN). Reconnects automatically
    on disconnection.
    """
    opts = _get_output_opts(ctx)

    from augint_tools.proxy.serve import run_proxy_server

    try:
        run_proxy_server(
            relay_host=relay_host,
            relay_port=relay_port,
            remote_port=remote_port,
            local_socks_port=local_port,
            key_file=key_file,
            json_mode=opts["json_mode"],
        )
    except KeyboardInterrupt:
        emit_response(
            CommandResponse.ok("proxy serve", "infra", "Proxy server stopped"),
            **opts,
        )


@proxy_group.command("connect")
@click.option("--local-port", default=1080, show_default=True, help="Local port for SOCKS proxy.")
@click.option(
    "--start-instance/--no-start-instance",
    default=True,
    help="Start EC2 instance if stopped.",
)
@click.option(
    "--wait-timeout", default=180, show_default=True, help="Seconds to wait for instance."
)
@click.pass_context
def connect_cmd(ctx, local_port, start_instance, wait_timeout):
    """Run on personal laptop: connect through the relay.

    Checks the EC2 instance state (starting it if needed), waits for the
    SSM agent, then opens an SSM port-forward to a local SOCKS proxy port.
    """
    opts = _get_output_opts(ctx)
    profile = ctx.obj.get("aws_profile")

    from augint_tools.proxy.connect import establish_connection

    try:
        result = establish_connection(
            profile=profile,
            local_port=local_port,
            start_instance_flag=start_instance,
            wait_timeout=wait_timeout,
        )
        emit_response(
            CommandResponse.ok(
                "proxy connect",
                "infra",
                f"Connected. SOCKS proxy on localhost:{local_port}",
                result=result,
                next_actions=[
                    f"Configure browser: SOCKS5 proxy localhost:{local_port}",
                    f"Or use: curl --proxy socks5h://localhost:{local_port} http://internal-site",
                ],
            ),
            **opts,
        )
    except Exception as exc:
        emit_response(
            CommandResponse.error("proxy connect", "infra", str(exc)),
            **opts,
        )
        sys.exit(1)


@proxy_group.command("status")
@click.pass_context
def status_cmd(ctx):
    """Show relay infrastructure and connection status."""
    opts = _get_output_opts(ctx)
    profile = ctx.obj.get("aws_profile")

    from augint_tools.proxy.status import get_status

    try:
        status = get_status(profile=profile)
        emit_response(
            CommandResponse.ok(
                "proxy status",
                "infra",
                status["summary"],
                result=status,
            ),
            **opts,
        )
    except Exception as exc:
        emit_response(
            CommandResponse.error("proxy status", "infra", str(exc)),
            **opts,
        )
        sys.exit(1)


# ── infra subgroup ──────────────────────────────────────────────────────


@proxy_group.group("infra")
@click.pass_context
def infra_group(ctx):
    """Manage relay infrastructure (deploy / destroy)."""
    ctx.ensure_object(dict)


@infra_group.command("deploy")
@click.option("--domain", default=None, help="Custom domain for relay (optional).")
@click.option("--vpc-id", default=None, help="VPC ID (uses default VPC if omitted).")
@click.option("--dry-run", is_flag=True, help="Show what would be deployed.")
@click.pass_context
def infra_deploy_cmd(ctx, domain, vpc_id, dry_run):
    """Deploy or update the relay infrastructure via CDK."""
    opts = _get_output_opts(ctx)
    profile = ctx.obj.get("aws_profile")

    from augint_tools.proxy.infra import deploy_stack

    try:
        result = deploy_stack(
            profile=profile,
            domain=domain,
            vpc_id=vpc_id,
            dry_run=dry_run,
        )
        verb = "Would deploy" if dry_run else "Deployed"
        emit_response(
            CommandResponse.ok(
                "proxy infra deploy",
                "infra",
                f"{verb} relay stack",
                result=result,
                next_actions=[
                    "Run: ai-tools proxy serve --relay-host <host>  (on work computer)",
                    "Run: ai-tools proxy connect                    (on personal laptop)",
                ],
            ),
            **opts,
        )
    except Exception as exc:
        emit_response(
            CommandResponse.error("proxy infra deploy", "infra", str(exc)),
            **opts,
        )
        sys.exit(1)


@infra_group.command("destroy")
@click.option("--force", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def infra_destroy_cmd(ctx, force):
    """Tear down the relay infrastructure."""
    opts = _get_output_opts(ctx)
    profile = ctx.obj.get("aws_profile")

    if not force and sys.stdin.isatty():
        if not click.confirm("This will destroy the proxy relay infrastructure. Continue?"):
            emit_response(
                CommandResponse.ok("proxy infra destroy", "infra", "Cancelled"),
                **opts,
            )
            return

    from augint_tools.proxy.infra import destroy_stack

    try:
        destroy_stack(profile=profile)
        emit_response(
            CommandResponse.ok("proxy infra destroy", "infra", "Infrastructure destroyed"),
            **opts,
        )
    except Exception as exc:
        emit_response(
            CommandResponse.error("proxy infra destroy", "infra", str(exc)),
            **opts,
        )
        sys.exit(1)
