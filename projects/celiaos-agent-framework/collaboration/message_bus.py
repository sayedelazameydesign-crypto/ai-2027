
import queue
import threading

class MessageBus:
    def __init__(self):
        self.topics = {} # topic_name -> list of subscribers
        self.lock = threading.Lock()

    def subscribe(self, topic, callback):
        with self.lock:
            if topic not in self.topics:
                self.topics[topic] = []
            self.topics[topic].append(callback)
            print(f"[Collaboration] Subscribed to topic: {topic}")

    def publish(self, topic, message):
        print(f"[Collaboration] Publishing to {topic}: {message}")
        if topic in self.topics:
            for callback in self.topics[topic]:
                # In a real system, this would be asynchronous
                callback(message)

    def send_direct(self, receiver_id, sender_id, content):
        print(f"[Collaboration] Direct message from {sender_id} to {receiver_id}: {content}")
        # Placeholder for direct messaging logic
