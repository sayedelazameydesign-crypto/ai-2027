
import queue
import threading

class RuntimeKernel:
    def __init__(self, worker_count=5):
        self.task_queue = queue.Queue()
        self.workers = []
        self.worker_count = worker_count
        self.is_running = False

    def boot(self):
        print("[Runtime] Booting CeliaOS Kernel...")
        self.is_running = True
        for i in range(self.worker_count):
            t = threading.Thread(target=self._worker_loop, args=(i,))
            t.daemon = True
            t.start()
            self.workers.append(t)

    def _worker_loop(self, worker_id):
        while self.is_running:
            try:
                task = self.task_queue.get(timeout=1)
                print(f"[Runtime] Worker-{worker_id} processing task: {task['id']}")
                task['func'](*task['args'])
                self.task_queue.task_done()
            except queue.Empty:
                continue

    def submit_task(self, task_id, func, *args):
        print(f"[Runtime] Submitting task {task_id} to queue")
        self.task_queue.put({"id": task_id, "func": func, "args": args})

    def shutdown(self):
        print("[Runtime] Shutting down kernel...")
        self.is_running = False
        for t in self.workers:
            t.join()

class TaskDispatcher:
    def __init__(self, kernel):
        self.kernel = kernel

    def dispatch(self, task_id, func, *args):
        self.kernel.submit_task(task_id, func, *args)
