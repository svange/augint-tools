"""SOCKS5 proxy server with reverse SSH tunnel for work computer."""

import asyncio
import socket
import struct

from loguru import logger


class Socks5Server:
    """Minimal SOCKS5 proxy server (RFC 1928)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 1080) -> None:
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a SOCKS5 client connection."""
        dest_writer: asyncio.StreamWriter | None = None
        try:
            # SOCKS5 greeting
            data = await reader.readexactly(2)
            if data[0] != 0x05:
                return

            nmethods = data[1]
            await reader.readexactly(nmethods)  # consume auth methods
            writer.write(b"\x05\x00")  # no auth required
            await writer.drain()

            # Connection request
            data = await reader.readexactly(4)
            _ver, cmd, _rsv, atyp = struct.unpack("!BBBB", data)

            if cmd != 0x01:  # only CONNECT
                writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
                await writer.drain()
                return

            # Parse destination address
            if atyp == 0x01:  # IPv4
                raw = await reader.readexactly(4)
                dest_addr = socket.inet_ntoa(raw)
            elif atyp == 0x03:  # Domain
                length = (await reader.readexactly(1))[0]
                dest_addr = (await reader.readexactly(length)).decode()
            elif atyp == 0x04:  # IPv6
                raw = await reader.readexactly(16)
                dest_addr = socket.inet_ntop(socket.AF_INET6, raw)
            else:
                return

            port_data = await reader.readexactly(2)
            dest_port = struct.unpack("!H", port_data)[0]

            logger.debug("SOCKS5 CONNECT {}:{}", dest_addr, dest_port)

            # Connect to destination
            dest_reader, dest_writer = await asyncio.open_connection(dest_addr, dest_port)

            # Success reply
            writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
            await writer.drain()

            # Relay data bidirectionally
            await asyncio.gather(
                _relay(reader, dest_writer),
                _relay(dest_reader, writer),
            )

        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            logger.debug("SOCKS5 connection closed: {}", exc)
            # Send general failure if we haven't replied yet
            try:
                writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6)
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            if dest_writer is not None:
                dest_writer.close()

    async def start(self) -> None:
        """Start the SOCKS5 server."""
        self._server = await asyncio.start_server(self.handle_client, self.host, self.port)
        logger.info("SOCKS5 proxy listening on {}:{}", self.host, self.port)
        await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop the SOCKS5 server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Relay data from *reader* to *writer* until EOF."""
    try:
        while True:
            data = await reader.read(8192)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def establish_reverse_tunnel(
    relay_host: str,
    relay_port: int,
    remote_port: int,
    local_port: int,
    key_file: str | None = None,
    reconnect_delay: float = 5.0,
) -> None:
    """Establish a persistent reverse SSH tunnel to the relay.

    Reconnects automatically on disconnection.
    """
    import asyncssh

    while True:
        try:
            logger.info(
                "Connecting to relay {}:{} (remote port {} -> local {})",
                relay_host,
                relay_port,
                remote_port,
                local_port,
            )
            async with asyncssh.connect(
                relay_host,
                port=relay_port,
                known_hosts=None,
                client_keys=[key_file] if key_file else None,
            ) as conn:
                listener = await conn.forward_remote_port("", remote_port, "localhost", local_port)
                logger.info("Reverse tunnel established")
                await listener.wait_closed()
        except (OSError, asyncssh.Error) as exc:
            logger.warning("Tunnel disconnected: {}. Reconnecting in {}s…", exc, reconnect_delay)
            await asyncio.sleep(reconnect_delay)


def run_proxy_server(
    relay_host: str,
    relay_port: int,
    remote_port: int,
    local_socks_port: int,
    key_file: str | None = None,
    json_mode: bool = False,
) -> None:
    """Run SOCKS proxy and reverse tunnel (blocking).

    Called from the CLI ``proxy serve`` command.
    """
    import click

    if not json_mode:
        click.echo(f"Starting SOCKS5 proxy on localhost:{local_socks_port}")
        click.echo(f"Connecting to relay {relay_host}:{relay_port}")
        click.echo(f"Remote port on relay: {remote_port}")
        click.echo("Press Ctrl+C to stop")

    async def _main() -> None:
        socks = Socks5Server(port=local_socks_port)
        await asyncio.gather(
            socks.start(),
            establish_reverse_tunnel(
                relay_host, relay_port, remote_port, local_socks_port, key_file
            ),
        )

    asyncio.run(_main())
