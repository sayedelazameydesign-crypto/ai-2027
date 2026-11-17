
class StateManager:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.state = "Idle"
        self.metadata = {}

    def set_state(self, new_state):
        self.state = new_state

    def update_metadata(self, key, value):
        self.metadata[key] = value

    def get_info(self):
        return {"id": self.agent_id, "state": self.state, "metadata": self.metadata}
