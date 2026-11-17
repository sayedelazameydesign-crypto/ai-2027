
class SessionManager:
    def __init__(self):
        self.sessions = {}

    def create_session(self, session_id):
        self.sessions[session_id] = {"status": "active"}

    def close_session(self, session_id):
        if session_id in self.sessions:
            self.sessions[session_id]["status"] = "closed"
