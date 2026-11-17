
from .base_agent import BaseAgent

class LearnerAgent(BaseAgent):
    def learn(self, context):
        print(f"[{self.state.name}] Saving knowledge from: {context.task_id}")
        # Logic to update Knowledge Graph
        context.add_knowledge_update({"pattern": "A leads to B", "new_rule": "If X then Y"})
        context.update_status("learned")
