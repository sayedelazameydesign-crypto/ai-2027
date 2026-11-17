
class EpisodicMemory:
    def __init__(self):
        self.episodes = [] # List of experiences

    def record(self, episode):
        print("[Memory] Recording new episode")
        self.episodes.append(episode)

class SemanticMemory:
    def __init__(self):
        self.concepts = {} # Knowledge graph placeholder

    def add_concept(self, concept, relations):
        print(f"[Memory] Adding concept: {concept}")
        self.concepts[concept] = relations

class ProceduralMemory:
    def __init__(self):
        self.skills = {} # How-to knowledge

    def learn_skill(self, skill_name, steps):
        print(f"[Memory] Learning new skill: {skill_name}")
        self.skills[skill_name] = steps

class WorkingMemory:
    def __init__(self):
        self.active_data = {}

    def load(self, data):
        self.active_data.update(data)
