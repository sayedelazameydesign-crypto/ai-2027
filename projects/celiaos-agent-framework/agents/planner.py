
from .base_agent import BaseAgent

class PlannerAgent(BaseAgent):
    def plan(self, context):
        print(f"[{self.state.name}] Planning sub-tasks for: {context.task_id}")
        # Logic to split task into sub-tasks
        context.add_sub_task("sub-1", "Researching topic")
        context.add_sub_task("sub-2", "Coding implementation")
        context.update_status("planned")
