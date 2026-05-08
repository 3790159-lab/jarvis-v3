from app.services.time_brain import get_time_context, dispatch_due_tasks
import json

print(json.dumps(get_time_context(), ensure_ascii=False, indent=2))
print(json.dumps(dispatch_due_tasks(limit=5), ensure_ascii=False, indent=2))