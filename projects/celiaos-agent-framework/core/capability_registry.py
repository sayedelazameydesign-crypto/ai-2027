
class CapabilityRegistry:
    def __init__(self):
        self.capabilities = []

    def register(self, capability):
        self.capabilities.append(capability)

    def has_capability(self, capability):
        return capability in self.capabilities
