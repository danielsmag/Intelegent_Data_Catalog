"""Notebook kernel routes. The channel socket is authorized by a ticket, not a bearer."""

from typing import Annotated, cast
from uuid import UUID

from dependency_injector.containers import DynamicContainer
from fastapi import APIRouter, Query, WebSocket
from starlette.responses import Response
from starlette.websockets import WebSocketState
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.dependencies import AccessDep, NotebookKernelDep
from intelegent_data_catalog.api.v1.schemas import KernelRead, KernelStatus, KernelTicketRead
from intelegent_data_catalog.exceptions import AuthenticationError
from intelegent_data_catalog.notebooks.channels import pump, reject
from intelegent_data_catalog.notebooks.directory import KernelLease
from intelegent_data_catalog.notebooks.gateway import KernelGatewayClient
from intelegent_data_catalog.notebooks.tickets import TicketClaims

router: APIRouter = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/nodes/{node_id}/kernel",
    response_model=KernelRead,
    status_code=201,
)
def start_kernel(
    workspace_id: UUID,
    node_id: UUID,
    service: NotebookKernelDep,
    access: AccessDep,
) -> KernelRead:
    """Start a PySpark kernel for one notebook file."""
    return service.start(workspace_id, node_id, access)


@router.delete("/kernels/{kernel_id}", status_code=204)
def stop_kernel(kernel_id: str, service: NotebookKernelDep, access: AccessDep) -> Response:
    """Stop a kernel owned by the caller."""
    service.stop(kernel_id, access)
    return Response(status_code=204)


@router.post("/kernels/{kernel_id}/interrupt", response_model=KernelStatus)
def interrupt_kernel(
    kernel_id: str,
    service: NotebookKernelDep,
    access: AccessDep,
) -> KernelStatus:
    """Interrupt the running cell."""
    return service.interrupt(kernel_id, access)


@router.post("/kernels/{kernel_id}/restart", response_model=KernelStatus)
def restart_kernel(
    kernel_id: str,
    service: NotebookKernelDep,
    access: AccessDep,
) -> KernelStatus:
    """Restart a kernel. Its id does not change."""
    return service.restart(kernel_id, access)


@router.post("/kernels/{kernel_id}/ticket", response_model=KernelTicketRead)
def refresh_ticket(
    kernel_id: str,
    service: NotebookKernelDep,
    access: AccessDep,
) -> KernelTicketRead:
    """Issue a new channel ticket after the previous one expires."""
    return service.ticket(kernel_id, access)


async def proxy_kernel_channels(websocket: WebSocket, kernel_id: str, ticket: str) -> None:
    """Proxy Jupyter channel frames after the ticket matches the kernel lease."""
    container: object = getattr(websocket.app.state, "container", None)
    if not isinstance(container, DynamicContainer):
        await reject(websocket, 1011)
        return
    app_container: AppContainer = cast(AppContainer, container)
    try:
        claims: TicketClaims = app_container.kernel_tickets().verify(ticket)
    except AuthenticationError:
        await reject(websocket, 4401)
        return
    if claims.kernel_id != kernel_id:
        await reject(websocket, 4401)
        return
    lease: KernelLease | None = app_container.kernel_directory().get(kernel_id)
    if lease is None or str(lease.user_id) != claims.user_id:
        await reject(websocket, 4404)
        return
    gateway: KernelGatewayClient = app_container.kernel_gateway()
    await websocket.accept()
    limit: int = app_container.settings().notebook.max_output_bytes
    try:
        async with connect(
            gateway.websocket_url(kernel_id),
            max_size=None,
            open_timeout=10,
        ) as upstream:
            await pump(websocket, upstream, limit)
    except WebSocketException:
        await websocket.close(code=1011)
    finally:
        await upstream_closed(websocket)


@router.websocket("/kernels/{kernel_id}/channels")
async def kernel_channels(
    websocket: WebSocket,
    kernel_id: str,
    ticket: Annotated[str, Query()] = "",
) -> None:
    """Proxy Jupyter channel frames after the ticket matches the kernel lease."""
    await proxy_kernel_channels(websocket, kernel_id, ticket)


async def upstream_closed(websocket: WebSocket) -> None:
    """Close the browser socket if it is still open."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.close()
    except RuntimeError:
        return
