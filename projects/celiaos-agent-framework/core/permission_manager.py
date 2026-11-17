
class PermissionManager:
    def __init__(self):
        self.permissions = {}

    def grant(self, resource, level):
        self.permissions[resource] = level

    def check(self, resource, required_level):
        return self.permissions.get(resource, 0) >= required_level
