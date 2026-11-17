
class Delegation:
    def __init__(self, communication_system):
        self.communication = communication_system
        self.specialized_agents = {} # role -> list of agent_ids

    def register_agent(self, agent_id, role):
        if role not in self.specialized_agents:
            self.specialized_agents[role] = []
        self.specialized_agents[role].append(agent_id)

    def find_agent_for_role(self, role):
        if role in self.specialized_agents and self.specialized_agents[role]:
            # Simple round-robin or random selection could be implemented here
            return self.specialized_agents[role][0]
        return None

    def delegate_task(self, from_agent_id, role, task_description, task_id):
        target_agent_id = self.find_agent_for_role(role)
        if target_agent_id:
            self.communication.send_message(
                sender_id=from_agent_id,
                receiver_id=target_agent_id,
                message_type="task_request",
                content=task_description,
                task_id=task_id
            )
            return target_agent_id
        return None
