
from .base_agent import BaseAgent

class ReporterAgent(BaseAgent):
    def report(self, context):
        print(f"[{self.state.name}] Generating final report for: {context.task_id}")
        context.update_status("reporting")
        report = {
            "task_id": context.task_id,
            "summary": f"Final report for '{context.user_request}'",
            "success_rate": "100%",
            "next_steps": "Wait for user input"
        }
        return report
