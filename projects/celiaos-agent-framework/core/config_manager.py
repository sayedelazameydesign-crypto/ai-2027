
class ConfigManager:
    def __init__(self, default_config=None):
        self.config = default_config or {}

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
