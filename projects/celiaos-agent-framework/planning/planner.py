
class Planner:
    def __init__(self, decomposer, dag_builder, scheduler):
        self.decomposer = decomposer
        self.dag_builder = dag_builder
        self.scheduler = scheduler

    def create_plan(self, goal):
        print(f"[Planning] Creating plan for goal: {goal}")
        sub_tasks = self.decomposer.decompose(goal)
        dag = self.dag_builder.build(sub_tasks)
        schedule = self.scheduler.schedule(dag)
        return schedule
