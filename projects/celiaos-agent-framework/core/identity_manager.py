
class IdentityManager:
    def __init__(self, name, version="V1"):
        self.name = name
        self.version = version
        self.signature = f"{name}-{version}"

    def get_identity(self):
        return {"name": self.name, "version": self.version, "signature": self.signature}
