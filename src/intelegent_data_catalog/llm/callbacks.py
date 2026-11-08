"""Process-wide LangChain callbacks. Telemetry appends its handler at startup."""

from langchain_core.callbacks import BaseCallbackHandler

_HANDLERS: list[BaseCallbackHandler] = []


def register_llm_callback(handler: BaseCallbackHandler) -> None:
    """Keep ``handler`` for every later model run in this process."""
    _HANDLERS.append(handler)


def llm_callbacks() -> list[BaseCallbackHandler]:
    """Return the handlers installed so far."""
    return list(_HANDLERS)
