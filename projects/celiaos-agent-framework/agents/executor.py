
from .base_agent import BaseAgent

class ExecutorAgent(BaseAgent):
    def execute(self, context):
        print(f"[{self.state.name}] Executing tools for task: {context.task_id}")
        # Logic to call tools (Shell, Python, Browser)
        context.add_result("execution", {"output": "Success", "logs": "Executed shell command..."})
        context.update_status("executed")
