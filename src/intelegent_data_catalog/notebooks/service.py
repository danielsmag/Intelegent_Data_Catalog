"""Start and control notebook kernels for workspace files."""

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.api.v1.schemas import KernelRead, KernelStatus, KernelTicketRead
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError, NotebookDisabledError
from intelegent_data_catalog.notebooks.credentials import EnvCredentialResolver
from intelegent_data_catalog.notebooks.directory import KernelDirectory, KernelLease
from intelegent_data_catalog.notebooks.gateway import KernelGateway
from intelegent_data_catalog.notebooks.kernel_env import KernelEnvBuilder, KernelEnvironment
from intelegent_data_catalog.notebooks.tickets import KernelTickets
from intelegent_data_catalog.persistence.models import WorkspaceNodeRow
from intelegent_data_catalog.registry.files import WorkspaceFileService


class NotebookKernelService:
    """Checks file access, builds kernel env, and records who owns the kernel."""

    def __init__(
        self,
        session: Session,
        files: WorkspaceFileService,
        gateway: KernelGateway,
        directory: KernelDirectory,
        tickets: KernelTickets,
        settings: AppSettings,
        logger: logging.Logger,
    ) -> None:
        self._session: Session = session
        self._files: WorkspaceFileService = files
        self._gateway: KernelGateway = gateway
        self._directory: KernelDirectory = directory
        self._tickets: KernelTickets = tickets
        self._settings: AppSettings = settings
        self._logger: logging.Logger = logger

    def start(self, workspace_id: UUID, node_id: UUID, access: AccessContext) -> KernelRead:
        """Start a kernel for one notebook, or return the one already running."""
        self._require_notebook(workspace_id, node_id, access)
        if not self._settings.notebook.enabled:
            raise NotebookDisabledError()
        existing: KernelLease | None = self._directory.find_for_node(access.user_id, node_id)
        if existing is not None:
            if self._gateway.exists(existing.kernel_id):
                return self._read(existing)
            self._directory.forget(existing.kernel_id)
        environment: KernelEnvironment = KernelEnvBuilder(
            self._session,
            self._settings,
            EnvCredentialResolver(self._settings.credentials),
        ).build(access)
        kernel_id: str = self._gateway.start(dict(environment.variables))
        lease: KernelLease = KernelLease(
            kernel_id=kernel_id,
            user_id=access.user_id,
            subject=access.principal.subject,
            workspace_id=workspace_id,
            node_id=node_id,
            environment=environment,
        )
        self._directory.remember(lease)
        self._logger.info(
            "notebook kernel started user=%s workspace=%s node=%s kernel=%s catalogs=%s",
            access.principal.subject,
            workspace_id,
            node_id,
            kernel_id,
            ",".join(environment.spark_catalogs),
        )
        return self._read(lease)

    def stop(self, kernel_id: str, access: AccessContext) -> None:
        """Delete a kernel the caller owns."""
        lease: KernelLease = self._lease(kernel_id, access)
        self._gateway.stop(lease.kernel_id)
        self._directory.forget(lease.kernel_id)
        self._logger.info(
            "notebook kernel stopped user=%s kernel=%s",
            access.principal.subject,
            kernel_id,
        )

    def interrupt(self, kernel_id: str, access: AccessContext) -> KernelStatus:
        """Interrupt the current cell."""
        lease: KernelLease = self._lease(kernel_id, access)
        self._gateway.interrupt(lease.kernel_id)
        return KernelStatus(kernel_id=lease.kernel_id, status="interrupted")

    def restart(self, kernel_id: str, access: AccessContext) -> KernelStatus:
        """Restart a kernel. The id and the original environment stay."""
        lease: KernelLease = self._lease(kernel_id, access)
        self._gateway.restart(lease.kernel_id)
        self._logger.info(
            "notebook kernel restarted user=%s kernel=%s",
            access.principal.subject,
            kernel_id,
        )
        return KernelStatus(kernel_id=lease.kernel_id, status="restarting")

    def ticket(self, kernel_id: str, access: AccessContext) -> KernelTicketRead:
        """Issue a fresh channel ticket for a kernel the caller owns."""
        lease: KernelLease = self._lease(kernel_id, access)
        return KernelTicketRead(
            ticket=self._tickets.sign(lease.kernel_id, str(lease.user_id)),
            ws_path=_ws_path(lease.kernel_id),
        )

    def _read(self, lease: KernelLease) -> KernelRead:
        user_id: str = str(lease.user_id)
        return KernelRead(
            kernel_id=lease.kernel_id,
            ticket=self._tickets.sign(lease.kernel_id, user_id),
            session_token=self._tickets.sign(lease.kernel_id, user_id, _SESSION_TTL_SECONDS),
            ws_path=_ws_path(lease.kernel_id),
            engines=list(lease.environment.engines),
            spark_catalogs=list(lease.environment.spark_catalogs),
            warnings=list(lease.environment.warnings),
        )

    def _lease(self, kernel_id: str, access: AccessContext) -> KernelLease:
        lease: KernelLease | None = self._directory.get(kernel_id)
        if lease is None:
            raise EntityNotFoundError("kernel", kernel_id)
        if lease.user_id != access.user_id and not access.principal.is_admin:
            raise EntityNotFoundError("kernel", kernel_id)
        if not access.is_member(lease.workspace_id):
            raise EntityNotFoundError("kernel", kernel_id)
        return lease

    def _require_notebook(self, workspace_id: UUID, node_id: UUID, access: AccessContext) -> None:
        node: WorkspaceNodeRow = self._files.require_visible_file(workspace_id, node_id, access)
        if not node.name.lower().endswith(".ipynb"):
            raise EntityNotFoundError("notebook", str(node_id))


def _ws_path(kernel_id: str) -> str:
    return f"/api/v1/kernels/{kernel_id}/channels"


_SESSION_TTL_SECONDS: int = 8 * 60 * 60
