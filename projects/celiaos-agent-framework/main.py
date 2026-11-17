
import sys
import os
import time

# Add the current directory to sys.path to allow relative imports in this simulation
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core import BaseAgent
from planning import Planner, TaskDecomposer, DAGBuilder, Scheduler, WorkflowEngine
from execution import ActionExecutor, PythonExecutor, ShellExecutor, BrowserExecutor
from collaboration import AgentRegistry, MessageBus, DelegationEngine
from runtime import RuntimeKernel

def main():
    print("--- CeliaOS V2 Multi-Agent Collaboration Simulation ---")
    
    # 1. Initialize Global Bus and Kernel
    bus = MessageBus()
    kernel = RuntimeKernel(worker_count=3)
    kernel.boot()
    
    # 2. Initialize Master Agent
    master = BaseAgent("Celia-Master")
    
    # 3. Setup Components for Simulation
    decomposer = TaskDecomposer()
    dag_builder = DAGBuilder()
    scheduler = Scheduler()
    planner = Planner(decomposer, dag_builder, scheduler)
    
    python_exec = PythonExecutor()
    executor = ActionExecutor({"python": python_exec})
    
    # 4. Initialize Workflow Engine
    workflow = WorkflowEngine(planner, executor)
    
    # 5. Register another agent to simulate collaboration
    master.registry.register("coding-agent-001", "Coder", "Coding", ["python", "javascript"])
    
    # 6. Simulate a mission with delegation and workflow
    mission = "Create a secure API for CeliaOS using Python"
    print(f"\n[Mission Start] {mission}\n")
    
    # Phase: Reasoning & Planning
    master.reasoner.reason(mission)
    
    # Phase: Delegation (V2 Core Feature)
    print("\n[V2 Feature] Delegating sub-task to Coding Agent...")
    master.delegator.delegate("Write API endpoints", "python", "Celia-Master")
    
    # Phase: Workflow Execution with Retry/Timeout
    print("\n[V2 Feature] Running Workflow with Reliability Policies...")
    result = workflow.execute_workflow(mission)
    
    # 7. Reporting
    print("\n--- Mission Summary ---")
    import json
    print(json.dumps(result, indent=4))
    
    # Cleanup
    kernel.is_running = False
    print("\nCeliaOS V2 Simulation Finished Successfully.")

if __name__ == "__main__":
    main()
