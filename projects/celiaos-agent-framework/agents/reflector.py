
from .base_agent import BaseAgent

class ReflectorAgent(BaseAgent):
    def reflect(self, context):
        print(f"[{self.state.name}] Self-reflection on task: {context.task_id}")
        # Logic to identify errors and improvements
        context.add_feedback("Everything went smoothly, but could be faster.")
        context.update_status("reflected")
