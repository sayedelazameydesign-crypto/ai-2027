import os
import json
from datetime import datetime

def save_to_drive(task_name, result):
    # مسار الأرشفة في درايف
    base_path = "/content/drive/MyDrive/AI_DevAgent/projects/Library_Backend/research_logs"
    os.makedirs(base_path, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(base_path, f"{task_name[:10]}_{timestamp}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump({"task": task_name, "result": str(result)}, f, ensure_ascii=False)
    return file_path
