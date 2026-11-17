
from .tool_executor import ToolExecutor

class ShellExecutor(ToolExecutor):
    def execute(self, params):
        print(f"[Execution] Executing Shell command: {params}")
        return "Shell result"
