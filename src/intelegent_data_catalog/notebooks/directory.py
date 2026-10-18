"""In-memory map of kernel id to the user who started it."""

import threading
from dataclasses import dataclass
from uuid import UUID

from intelegent_data_catalog.notebooks.kernel_env import KernelEnvironment


@dataclass(frozen=True, slots=True)
class KernelLease:
    """One running kernel owned by one user and one notebook file."""

    kernel_id: str
    user_id: UUID
    subject: str
    workspace_id: UUID
    node_id: UUID
    environment: KernelEnvironment


class KernelDirectory:
    """Process-local kernel leases. A restarted API forgets them."""

    def __init__(self) -> None:
        self._leases: dict[str, KernelLease] = {}
        self._by_node: dict[tuple[UUID, UUID], str] = {}
        self._lock: threading.Lock = threading.Lock()

    def find_for_node(self, user_id: UUID, node_id: UUID) -> KernelLease | None:
        """Return the kernel this user already has for a notebook."""
        with self._lock:
            kernel_id: str | None = self._by_node.get((user_id, node_id))
            if kernel_id is None:
                return None
            return self._leases.get(kernel_id)

    def remember(self, lease: KernelLease) -> None:
        """Store a lease, replacing any previous kernel for the same notebook."""
        with self._lock:
            previous: str | None = self._by_node.get((lease.user_id, lease.node_id))
            if previous is not None and previous != lease.kernel_id:
                self._leases.pop(previous, None)
            self._leases[lease.kernel_id] = lease
            self._by_node[(lease.user_id, lease.node_id)] = lease.kernel_id

    def get(self, kernel_id: str) -> KernelLease | None:
        """Return one lease."""
        with self._lock:
            return self._leases.get(kernel_id)

    def forget(self, kernel_id: str) -> None:
        """Drop a lease. Missing ids are ignored."""
        with self._lock:
            lease: KernelLease | None = self._leases.pop(kernel_id, None)
            if lease is None:
                return
            current: str | None = self._by_node.get((lease.user_id, lease.node_id))
            if current == kernel_id:
                self._by_node.pop((lease.user_id, lease.node_id), None)
