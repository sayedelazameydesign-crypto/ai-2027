
class ShortTermMemory:
    def __init__(self):
        self.buffer = []

    def store(self, item):
        self.buffer.append(item)

    def retrieve(self):
        return self.buffer

    def clear(self):
        self.buffer = []
