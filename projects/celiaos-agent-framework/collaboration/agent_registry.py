
class AgentRegistry:
    def __init__(self):
        self.agents = {} # agent_id -> agent_metadata

    def register(self, agent_id, name, role, capabilities):
        print(f"[Collaboration] Registering agent: {name} ({role})")
        self.agents[agent_id] = {
            "name": name,
            "role": role,
            "capabilities": capabilities,
            "status": "online"
        }

    def discover(self, capability=None):
        if not capability:
            return self.agents
        return {aid: meta for aid, meta in self.agents.items() if capability in meta["capabilities"]}

    def get_agent_info(self, agent_id):
        return self.agents.get(agent_id)
