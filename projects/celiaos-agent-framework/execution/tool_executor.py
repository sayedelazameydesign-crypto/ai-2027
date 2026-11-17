
class ToolExecutor:
    def execute(self, params):
        raise NotImplementedError("Subclasses must implement execute()")

class PythonExecutor(ToolExecutor):
    def execute(self, params):
        print(f"[Execution] Executing Python code: {params}")
        return "Python result"

class ShellExecutor(ToolExecutor):
    def execute(self, params):
        print(f"[Execution] Executing Shell command: {params}")
        return "Shell result"

class BrowserExecutor(ToolExecutor):
    def execute(self, params):
        print(f"[Execution] Navigating Browser to: {params}")
        return "Browser result"
