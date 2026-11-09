"""Business errors the domain raises without depending on the MCP SDK."""


class ToolInputError(Exception):
    """A business error the model can correct, returned as coded text."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        # Only the code goes to Exception.args: message and hint may carry
        # user data and must not surface in any log or traceback.
        super().__init__(code)
        self.code = code
        self.message = message
        self.hint = hint

    @property
    def text(self) -> str:
        text = f"{self.code}: {self.message}"
        return f"{text} {self.hint}" if self.hint else text
