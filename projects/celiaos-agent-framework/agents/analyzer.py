
from .base_agent import BaseAgent

class AnalyzerAgent(BaseAgent):
    def analyze(self, context):
        print(f"[{self.state.name}] Deep analysis of: {context.user_request}")
        # Logic to extract constraints and requirements
        context.add_result("analysis", {"goal": "Extracted goal", "constraints": ["Constraint 1"]})
        context.update_status("analyzed")
