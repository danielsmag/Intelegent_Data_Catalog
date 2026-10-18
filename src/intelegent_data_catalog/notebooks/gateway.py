"""HTTP client for the Jupyter kernel gateway."""

from typing import Protocol
from urllib.parse import quote

import httpx

from intelegent_data_catalog.exceptions import NotebookGatewayError


class KernelGateway(Protocol):
    """Starts and controls one kernel process."""

    def start(self, env: dict[str, str]) -> str:
        """Create a kernel and return its id."""

    def exists(self, kernel_id: str) -> bool:
        """Return whether the gateway still has this kernel."""

    def stop(self, kernel_id: str) -> None:
        """Delete a kernel. A missing kernel is not an error."""

    def interrupt(self, kernel_id: str) -> None:
        """Ask the kernel to stop the current cell."""

    def restart(self, kernel_id: str) -> None:
        """Restart a kernel, keeping its id and environment."""


class KernelGatewayClient:
    """Talks to Jupyter Kernel Gateway with its token scheme."""

    def __init__(self, http: httpx.Client, base_url: str, token: str, kernel_name: str) -> None:
        self._http: httpx.Client = http
        self._base: str = base_url.rstrip("/")
        self._token: str = token
        self._kernel_name: str = kernel_name

    def start(self, env: dict[str, str]) -> str:
        """Create a kernel and return its id."""
        response: httpx.Response = self._send(
            "POST",
            "/api/kernels",
            {"name": self._kernel_name, "env": env},
        )
        self._raise(response)
        payload: object = response.json()
        if not isinstance(payload, dict):
            raise NotebookGatewayError("kernel gateway returned no kernel id")
        kernel_id: object = payload.get("id")
        if not isinstance(kernel_id, str) or kernel_id == "":
            raise NotebookGatewayError("kernel gateway returned no kernel id")
        return kernel_id

    def exists(self, kernel_id: str) -> bool:
        """Return whether the gateway still has this kernel."""
        response: httpx.Response = self._send("GET", f"/api/kernels/{quote(kernel_id, safe='')}")
        if response.status_code == 404:
            return False
        if response.status_code == 403:
            return True
        self._raise(response)
        return True

    def stop(self, kernel_id: str) -> None:
        """Delete a kernel. A missing kernel is not an error."""
        response: httpx.Response = self._send(
            "DELETE",
            f"/api/kernels/{quote(kernel_id, safe='')}",
        )
        if response.status_code == 404:
            return
        self._raise(response)

    def interrupt(self, kernel_id: str) -> None:
        """Ask the kernel to stop the current cell."""
        response: httpx.Response = self._send(
            "POST",
            f"/api/kernels/{quote(kernel_id, safe='')}/interrupt",
        )
        self._raise(response)

    def restart(self, kernel_id: str) -> None:
        """Restart a kernel, keeping its id and environment."""
        response: httpx.Response = self._send(
            "POST",
            f"/api/kernels/{quote(kernel_id, safe='')}/restart",
        )
        self._raise(response)

    def websocket_url(self, kernel_id: str) -> str:
        """Channel URL the API opens toward the gateway."""
        base: str = self._base
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        kernel: str = quote(kernel_id, safe="")
        token: str = quote(self._token, safe="")
        return f"{base}/api/kernels/{kernel}/channels?token={token}"

    def _send(self, method: str, path: str, body: object | None = None) -> httpx.Response:
        try:
            return self._http.request(
                method,
                f"{self._base}{path}",
                headers={"Authorization": f"token {self._token}"},
                json=body,
            )
        except httpx.HTTPError as err:
            raise NotebookGatewayError("notebook kernel gateway is unavailable") from err

    def _raise(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise NotebookGatewayError(_detail(response))


def _detail(response: httpx.Response) -> str:
    try:
        payload: object = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message: object = payload.get("message", payload.get("detail", ""))
        if isinstance(message, str) and message.strip() != "":
            return message.strip()[:300]
    return f"kernel gateway returned {response.status_code}"
