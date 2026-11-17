
import json

class Communication:
    def __init__(self):
        self.message_log = []

    def send_message(self, sender_id, receiver_id, message_type, content, task_id=None):
        message = {
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "message_type": message_type, # e.g., "task_request", "result", "feedback"
            "content": content,
            "task_id": task_id,
            "timestamp": "<timestamp_placeholder>" # In a real system, use datetime.now()
        }
        self.message_log.append(message)
        # In a real system, this would involve actual message passing mechanisms (e.g., queues, sockets)
        print(f"[Communication] Sent from {sender_id} to {receiver_id}: {message_type} - {content}")
        return True

    def get_messages_for_agent(self, agent_id):
        # In a real system, this would retrieve messages from an agent's inbox
        return [msg for msg in self.message_log if msg["receiver_id"] == agent_id]

    def get_all_messages(self):
        return self.message_log

    def clear_log(self):
        self.message_log = []
