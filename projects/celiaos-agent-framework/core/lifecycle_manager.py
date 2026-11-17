
class LifecycleManager:
    def __init__(self):
        self.current_stage = "Initialization"
        self.history = []

    def transition_to(self, stage):
        print(f"[Lifecycle] Transitioning from {self.current_stage} to {stage}")
        self.history.append(self.current_stage)
        self.current_stage = stage

    def get_status(self):
        return self.current_stage
