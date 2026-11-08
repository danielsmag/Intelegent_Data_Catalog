"""Errors the model is asked to correct inside one graph run."""


class ToolRetry(Exception):
    """A tool or output check failed. The message is sent back to the model."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message
