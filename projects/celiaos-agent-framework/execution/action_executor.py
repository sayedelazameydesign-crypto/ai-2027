
class ActionExecutor:
    def __init__(self, tools):
        self.tools = tools

    def run(self, action_name, params):
        print(f"[Execution] Running action: {action_name}")
        if action_name in self.tools:
            return self.tools[action_name].execute(params)
        return f"Error: Action {action_name} not found"
