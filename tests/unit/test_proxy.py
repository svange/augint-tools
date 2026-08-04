"""Tests for proxy command group."""

import asyncio
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
from augint_tools.proxy.serve import Socks5Server


class TestProxyCLI:
    """CLI surface tests – no AWS credentials needed."""

    def test_proxy_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "--help"])
        assert result.exit_code == 0
        assert "On-demand EC2 relay" in result.output

    def test_proxy_shows_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "--help"])
        assert "serve" in result.output
        assert "connect" in result.output
        assert "status" in result.output
        assert "infra" in result.output

    def test_serve_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "serve", "--help"])
        assert result.exit_code == 0
        assert "--relay-host" in result.output
        assert "--relay-port" in result.output
        assert "--remote-port" in result.output
        assert "--local-port" in result.output
        assert "--key-file" in result.output

    def test_serve_requires_relay_host(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "serve"])
        assert result.exit_code != 0
        assert "relay-host" in result.output.lower()

    def test_connect_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "connect", "--help"])
        assert result.exit_code == 0
        assert "--local-port" in result.output
        assert "--start-instance" in result.output
        assert "--wait-timeout" in result.output

    def test_status_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "status", "--help"])
        assert result.exit_code == 0
        assert "relay infrastructure" in result.output.lower()

    def test_infra_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "infra", "--help"])
        assert result.exit_code == 0
        assert "deploy" in result.output
        assert "destroy" in result.output

    def test_infra_deploy_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "infra", "deploy", "--help"])
        assert result.exit_code == 0
        assert "--domain" in result.output
        assert "--vpc-id" in result.output
        assert "--dry-run" in result.output

    def test_infra_destroy_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "infra", "destroy", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output

    def test_proxy_in_root_help(self):
        """proxy command appears in the root ai-tools --help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "proxy" in result.output

    def test_profile_option_accepted(self):
        """--profile is accepted at the group level."""
        runner = CliRunner()
        result = runner.invoke(cli, ["proxy", "--profile", "sandbox", "--help"])
        assert result.exit_code == 0


class TestProxyStatusMocked:
    """Status command with mocked AWS calls."""

    @patch("augint_tools.proxy.status._aws_cmd")
    def test_status_no_stack(self, mock_aws):
        import subprocess

        mock_aws.side_effect = subprocess.CalledProcessError(255, "aws")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "proxy", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["result"]["stack_exists"] is False

    @patch("augint_tools.proxy.status._aws_cmd")
    def test_status_running(self, mock_aws):
        import subprocess

        # Mock three sequential calls: describe-stacks, describe-instances, describe-instance-info
        stack_response = json.dumps(
            {
                "Stacks": [
                    {
                        "StackStatus": "CREATE_COMPLETE",
                        "Outputs": [
                            {"OutputKey": "InstanceId", "OutputValue": "i-abc123"},
                            {"OutputKey": "SecurityGroupId", "OutputValue": "sg-xyz"},
                        ],
                    }
                ]
            }
        )
        instance_response = json.dumps(
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "State": {"Name": "running"},
                                "LaunchTime": "2026-08-04T10:00:00Z",
                            }
                        ]
                    }
                ]
            }
        )
        ssm_response = json.dumps({"InstanceInformationList": [{"PingStatus": "Online"}]})

        mock_aws.side_effect = [
            subprocess.CompletedProcess([], 0, stdout=stack_response),
            subprocess.CompletedProcess([], 0, stdout=instance_response),
            subprocess.CompletedProcess([], 0, stdout=ssm_response),
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "proxy", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["result"]["ec2_state"] == "running"
        assert data["result"]["ssm_status"] == "Online"
        assert data["result"]["ec2_instance_id"] == "i-abc123"


class TestProxyConnectMocked:
    """Connect command with mocked AWS calls."""

    @patch("augint_tools.proxy.connect._aws_cmd")
    def test_connect_no_stack(self, mock_aws):
        import subprocess

        mock_aws.side_effect = subprocess.CalledProcessError(255, "aws")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "proxy", "connect"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"


class TestSocks5Server:
    """Unit tests for the SOCKS5 protocol implementation."""

    def test_server_init(self):
        server = Socks5Server(host="127.0.0.1", port=9999)
        assert server.host == "127.0.0.1"
        assert server.port == 9999
        assert server._server is None

    def test_server_default_port(self):
        server = Socks5Server()
        assert server.port == 1080

    @pytest.mark.timeout(5)
    def test_handle_bad_version(self):
        """Non-SOCKS5 greeting is rejected."""
        server = Socks5Server()

        async def _run():
            reader = AsyncMock(spec=asyncio.StreamReader)
            writer = MagicMock(spec=asyncio.StreamWriter)
            writer.close = MagicMock()

            # Send SOCKS4 version byte
            reader.readexactly = AsyncMock(return_value=b"\x04\x01")

            await server.handle_client(reader, writer)
            writer.close.assert_called()

        asyncio.run(_run())

    @pytest.mark.timeout(5)
    def test_handle_unsupported_cmd(self):
        """Non-CONNECT commands get an error reply."""
        server = Socks5Server()

        async def _run():
            reader = AsyncMock(spec=asyncio.StreamReader)
            writer = MagicMock(spec=asyncio.StreamWriter)
            writer.close = MagicMock()
            writer.drain = AsyncMock()
            writer.write = MagicMock()

            call_count = 0

            async def _readexactly(n):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return b"\x05\x01"  # greeting: v5, 1 method
                if call_count == 2:
                    return b"\x00"  # no-auth method
                if call_count == 3:
                    # BIND command (0x02) instead of CONNECT (0x01)
                    return struct.pack("!BBBB", 0x05, 0x02, 0x00, 0x01)
                return b""

            reader.readexactly = AsyncMock(side_effect=_readexactly)

            await server.handle_client(reader, writer)

            # Should have sent error reply (command not supported = 0x07)
            calls = writer.write.call_args_list
            assert any(b"\x05\x07" in call.args[0] for call in calls)

        asyncio.run(_run())
