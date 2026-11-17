
class ContextManager:
    def __init__(self):
        self.active_context = {}

    def set_context(self, task_id, data):
        self.active_context[task_id] = data

    def get_context(self, task_id):
        return self.active_context.get(task_id, {})
