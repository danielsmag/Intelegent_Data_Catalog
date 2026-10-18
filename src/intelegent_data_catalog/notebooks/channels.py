"""Proxy notebook channel sockets to the kernel gateway."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed


def truncated_output(raw: str) -> str:
    """Replace an oversized kernel frame with a short stderr message."""
    parent: dict[str, object] = {}
    try:
        parsed: object = json.loads(raw) if raw != "" else {}
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        header: object = parsed.get("header")
        if isinstance(header, dict):
            key: object
            value: object
            for key, value in header.items():
                parent[str(key)] = value
    message: dict[str, object] = {
        "header": {
            "msg_id": str(uuid4()),
            "session": "catalog",
            "username": "catalog",
            "date": datetime.now(UTC).isoformat(),
            "msg_type": "stream",
            "version": "5.3",
        },
        "parent_header": parent,
        "metadata": {},
        "content": {"name": "stderr", "text": "output exceeded the notebook limit\n"},
        "channel": "iopub",
    }
    return json.dumps(message)


async def pump(websocket: WebSocket, upstream: ClientConnection, limit: int) -> None:
    """Copy frames both ways until either side closes."""

    async def client_to_gateway() -> None:
        while True:
            incoming: object = await websocket.receive()
            if not isinstance(incoming, dict):
                return
            if incoming.get("type") == "websocket.disconnect":
                return
            text: object = incoming.get("text")
            data: object = incoming.get("bytes")
            if isinstance(text, str):
                await upstream.send(text)
            elif isinstance(data, bytes):
                await upstream.send(data)

    async def gateway_to_client() -> None:
        message: str | bytes
        async for message in upstream:
            if isinstance(message, bytes):
                if len(message) > limit:
                    await websocket.send_text(truncated_output(""))
                    continue
                await websocket.send_bytes(message)
                continue
            if len(message.encode("utf-8")) > limit:
                await websocket.send_text(truncated_output(message))
                continue
            await websocket.send_text(message)

    tasks: set[asyncio.Task[None]] = {
        asyncio.create_task(client_to_gateway()),
        asyncio.create_task(gateway_to_client()),
    }
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    task: asyncio.Task[None]
    for task in pending:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, WebSocketDisconnect, ConnectionClosed):
            continue


async def reject(websocket: WebSocket, code: int) -> None:
    """Accept, then close, so the browser observes the close code."""
    await websocket.accept()
    await websocket.close(code=code)
