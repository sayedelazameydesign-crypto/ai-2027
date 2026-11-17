
import collections
import threading

class TaskQueue:
    def __init__(self):
        self.queue = collections.deque()
        self.lock = threading.Lock()
        self.not_empty = threading.Condition(self.lock)

    def add_task(self, task_context):
        with self.lock:
            self.queue.append(task_context)
            self.not_empty.notify()

    def get_task(self):
        with self.not_empty:
            while not self.queue:
                self.not_empty.wait()
            return self.queue.popleft()

    def is_empty(self):
        with self.lock:
            return not bool(self.queue)

    def size(self):
        with self.lock:
            return len(self.queue)
