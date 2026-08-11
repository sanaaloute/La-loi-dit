"""TCP relay exposing the WSL-local Ollama to Docker containers.

Ollama serves on ``127.0.0.1:11434`` inside the WSL VM. Containers on the
``legal-net`` bridge cannot reach a loopback-only service, so this relay —
run as the ``ollama-relay`` compose service with ``network_mode: host`` —
listens on the bridge gateway and forwards to Ollama's loopback.

Bind address and ports are overridable via env so the service stays
declarative in docker-compose.yml:

- ``OLLAMA_RELAY_BIND`` (default ``172.18.0.1`` — the legal-net gateway)
- ``OLLAMA_RELAY_PORT`` (default ``11434``)
- ``OLLAMA_TARGET_HOST`` / ``OLLAMA_TARGET_PORT`` (default ``127.0.0.1:11434``)
"""

import asyncio
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ollama-relay")

BIND = os.environ.get("OLLAMA_RELAY_BIND", "172.18.0.1")
PORT = int(os.environ.get("OLLAMA_RELAY_PORT", "11434"))
TARGET_HOST = os.environ.get("OLLAMA_TARGET_HOST", "127.0.0.1")
TARGET_PORT = int(os.environ.get("OLLAMA_TARGET_PORT", "11434"))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        target_reader, target_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except OSError:
        logger.warning("ollama unreachable at %s:%s", TARGET_HOST, TARGET_PORT)
        client_writer.close()
        return
    await asyncio.gather(
        _pipe(client_reader, target_writer),
        _pipe(target_reader, client_writer),
    )


async def main() -> None:
    server = await asyncio.start_server(_handle, BIND, PORT)
    logger.info("relay listening on %s:%s -> %s:%s", BIND, PORT, TARGET_HOST, TARGET_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
