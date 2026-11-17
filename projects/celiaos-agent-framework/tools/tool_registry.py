
class ToolRegistry:
    def __init__(self):
        self.tools = {} # tool_name -> tool_metadata

    def register_tool(self, name, version, manifest):
        print(f"[Tools] Registering tool: {name} v{version}")
        self.tools[name] = {
            "version": version,
            "manifest": manifest,
            "status": "active"
        }

    def get_tool(self, name):
        return self.tools.get(name)

    def list_tools(self):
        return list(self.tools.keys())

class ToolManifest:
    def __init__(self, name, description, permissions):
        self.name = name
        self.description = description
        self.permissions = permissions # List of required permissions
