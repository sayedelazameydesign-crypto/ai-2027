
class AgentState:
    def __init__(self, agent_id, name, role, capabilities=None):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.capabilities = capabilities if capabilities is not None else []
        self.current_task_id = None
        self.status = "idle"
        self.performance_metrics = {"tasks_completed": 0, "errors": 0}

    def assign_task(self, task_id):
        self.current_task_id = task_id
        self.status = "busy"

    def complete_task(self, success=True):
        if success:
            self.performance_metrics["tasks_completed"] += 1
        else:
            self.performance_metrics["errors"] += 1
        self.current_task_id = None
        self.status = "idle"

    def update_status(self, new_status):
        self.status = new_status

    def __repr__(self):
        return f"AgentState(ID: {self.agent_id}, Name: {self.name}, Role: {self.role}, Status: {self.status})"
