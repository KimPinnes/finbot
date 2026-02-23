"""Localtunnel integration for exposing the local API server to the internet.

When ``TUNNEL_ENABLED=true``, a localtunnel subprocess is spawned alongside
the bot, creating a public HTTPS URL that forwards to the local API port.
This lets the GitHub-Pages-hosted Mini App reach the local backend during
development.

Requires the ``lt`` CLI (``npm install -g localtunnel``) or ``npx``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

logger = logging.getLogger(__name__)

_process: asyncio.subprocess.Process | None = None


async def start_tunnel(port: int, subdomain: str = "") -> str | None:
    """Start a localtunnel and return the public HTTPS URL, or *None* on failure."""
    global _process

    cmd: list[str]
    if shutil.which("lt"):
        cmd = ["lt", "--port", str(port)]
    elif shutil.which("npx"):
        cmd = ["npx", "--yes", "localtunnel", "--port", str(port)]
    else:
        logger.error(
            "TUNNEL_ENABLED=true but neither `lt` nor `npx` found on PATH. "
            "Install localtunnel: npm install -g localtunnel"
        )
        return None

    if subdomain:
        cmd.extend(["--subdomain", subdomain])

    logger.info("Starting tunnel: %s", " ".join(cmd))

    _process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    url = await _read_url(_process)
    if url:
        logger.info("Tunnel ready → %s", url)
    else:
        logger.error("Could not obtain tunnel URL — check localtunnel output above")
        await stop_tunnel()
    return url


async def _read_url(proc: asyncio.subprocess.Process) -> str | None:
    """Parse the public URL from localtunnel's stdout."""
    assert proc.stdout
    try:
        async with asyncio.timeout(30):
            async for raw in proc.stdout:
                line = raw.decode().strip()
                logger.debug("tunnel: %s", line)
                if "https://" in line:
                    for token in line.split():
                        if token.startswith("https://"):
                            return token.rstrip("/")
    except TimeoutError:
        logger.error("Timed out waiting for tunnel URL (30 s)")
    return None


async def stop_tunnel() -> None:
    """Terminate the tunnel subprocess if running."""
    global _process
    if _process is None or _process.returncode is not None:
        return
    logger.info("Stopping tunnel (pid %d)", _process.pid)
    _process.terminate()
    try:
        await asyncio.wait_for(_process.wait(), timeout=5)
    except TimeoutError:
        _process.kill()
    _process = None
