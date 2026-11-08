"""Deps for the tool node. They stay out of checkpointed graph state."""

from contextvars import ContextVar, Token

_DEPS: ContextVar[object | None] = ContextVar("llm_tool_deps", default=None)


def bind_tool_deps(deps: object) -> Token[object | None]:
    """Expose ``deps`` until the tool node returns."""
    return _DEPS.set(deps)


def reset_tool_deps(token: Token[object | None]) -> None:
    """Restore the previous deps."""
    _DEPS.reset(token)


def current_tool_deps() -> object:
    """Return the deps bound for the tool that is running."""
    deps: object | None = _DEPS.get()
    if deps is None:
        raise RuntimeError("catalog tool called outside a model run")
    return deps
