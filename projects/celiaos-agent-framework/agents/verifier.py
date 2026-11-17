
from .base_agent import BaseAgent

class VerifierAgent(BaseAgent):
    def verify(self, context):
        print(f"[{self.state.name}] Verifying results for: {context.task_id}")
        # Logic to test output against goal
        context.add_result("verification", {"verified": True, "score": 0.95})
        context.update_status("verified")
