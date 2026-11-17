
class TaskContext:
    def __init__(self, task_id, user_request, priority=None, deadline=None):
        self.task_id = task_id
        self.user_request = user_request
        self.priority = priority
        self.deadline = deadline
        self.status = "received"
        self.sub_tasks = []
        self.results = {}
        self.feedback = []
        self.knowledge_updates = []

    def update_status(self, new_status):
        self.status = new_status

    def add_sub_task(self, sub_task_id, description):
        self.sub_tasks.append({"id": sub_task_id, "description": description, "status": "pending"})

    def update_sub_task_status(self, sub_task_id, new_status, result=None):
        for task in self.sub_tasks:
            if task["id"] == sub_task_id:
                task["status"] = new_status
                if result:
                    task["result"] = result
                break

    def add_result(self, agent_name, result_data):
        self.results[agent_name] = result_data

    def add_feedback(self, feedback_message):
        self.feedback.append(feedback_message)

    def add_knowledge_update(self, knowledge_data):
        self.knowledge_updates.append(knowledge_data)

    def __repr__(self):
        return f"TaskContext(ID: {self.task_id}, Status: {self.status}, Request: '{self.user_request[:50]}...')"
