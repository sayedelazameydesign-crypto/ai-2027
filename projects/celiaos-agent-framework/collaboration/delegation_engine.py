
class DelegationEngine:
    def __init__(self, registry, message_bus):
        self.registry = registry
        self.message_bus = message_bus

    def delegate(self, task, required_capability, sender_id):
        print(f"[Collaboration] Attempting to delegate task: {task}")
        capable_agents = self.registry.discover(required_capability)
        
        if not capable_agents:
            print(f"[Collaboration] No agent found with capability: {required_capability}")
            return None
        
        # Simple selection: pick the first available agent
        target_id = list(capable_agents.keys())[0]
        target_name = capable_agents[target_id]["name"]
        
        print(f"[Collaboration] Delegating to {target_name} ({target_id})")
        self.message_bus.send_direct(target_id, sender_id, {"task": task, "type": "delegation"})
        return target_id
