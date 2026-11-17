
class SharedContext:
    def __init__(self, session_id):
        self.session_id = session_id
        self.data = {}
        self.lock = {} # Placeholder for thread-safe access if needed

    def update(self, key, value, agent_id):
        print(f"[Collaboration] SharedContext updated by {agent_id}: {key}")
        self.data[key] = {
            "value": value,
            "updated_by": agent_id
        }

    def get(self, key):
        return self.data.get(key)

    def get_all(self):
        return self.data
