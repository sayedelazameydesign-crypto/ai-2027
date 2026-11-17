
import uuid
from .task_context import TaskContext
from .agent_state import AgentState

class BaseAgent:
    def __init__(self, agent_id, name, role, communication, delegation=None):
        self.state = AgentState(agent_id, name, role)
        self.communication = communication
        self.delegation = delegation
        self.active_tasks = {}

    def run_cycle(self, task_input, priority=None, deadline=None):
        """The 9-stage operational cycle."""
        # Phase 1: Receive
        task_context = self.receive(task_input, priority, deadline)
        self.active_tasks[task_context.task_id] = task_context
        self.state.assign_task(task_context.task_id)

        # Phase 2: Analyze
        self.analyze(task_context)

        # Phase 3: Plan
        self.plan(task_context)

        # Phase 4: Delegate
        self.delegate(task_context)

        # Phase 5: Execute
        self.execute(task_context)

        # Phase 6: Verify
        self.verify(task_context)

        # Phase 7: Reflect
        self.reflect(task_context)

        # Phase 8: Learn
        self.learn(task_context)

        # Phase 9: Report
        report = self.report(task_context)
        
        self.state.complete_task(success=True) # Assuming success for the skeleton
        return report

    def receive(self, task_input, priority, deadline):
        task_id = str(uuid.uuid4())
        print(f"[{self.state.name}] Phase 1: Receiving task {task_id}")
        return TaskContext(task_id, task_input, priority, deadline)

    def analyze(self, context):
        print(f"[{self.state.name}] Phase 2: Analyzing task {context.task_id}")
        context.update_status("analyzing")
        # To be overridden by specialized agents

    def plan(self, context):
        print(f"[{self.state.name}] Phase 3: Planning task {context.task_id}")
        context.update_status("planning")
        # To be overridden by specialized agents

    def delegate(self, context):
        print(f"[{self.state.name}] Phase 4: Delegating task {context.task_id}")
        context.update_status("delegating")
        # To be overridden by specialized agents

    def execute(self, context):
        print(f"[{self.state.name}] Phase 5: Executing task {context.task_id}")
        context.update_status("executing")
        # To be overridden by specialized agents

    def verify(self, context):
        print(f"[{self.state.name}] Phase 6: Verifying task {context.task_id}")
        context.update_status("verifying")
        # To be overridden by specialized agents

    def reflect(self, context):
        print(f"[{self.state.name}] Phase 7: Reflecting on task {context.task_id}")
        context.update_status("reflecting")
        # To be overridden by specialized agents

    def learn(self, context):
        print(f"[{self.state.name}] Phase 8: Learning from task {context.task_id}")
        context.update_status("learning")
        # To be overridden by specialized agents

    def report(self, context):
        print(f"[{self.state.name}] Phase 9: Reporting task {context.task_id}")
        context.update_status("completed")
        report = {
            "task_id": context.task_id,
            "summary": f"Task '{context.user_request}' completed by {self.state.name}",
            "status": "success",
            "results": context.results
        }
        return report
