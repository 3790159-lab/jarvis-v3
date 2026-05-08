import json, os, datetime, uuid

ROOT = os.getcwd()
STATE = os.path.join(ROOT, "state", "jarvis_brain")
QUEUE = os.path.join(STATE, "action_queue_v6_4.json")
OUT   = os.path.join(ROOT, "jarvis_stage3_artifacts", "full_creator_v8_8")

os.makedirs(OUT, exist_ok=True)

def now():
    return datetime.datetime.utcnow().isoformat()

def load_queue():
    if os.path.exists(QUEUE):
        with open(QUEUE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"schema":"jarvis.action_queue.v6_4","items":[]}

def save_queue(q):
    with open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(q, f, indent=2, ensure_ascii=False)

def new_id():
    return "creator_" + str(uuid.uuid4())[:8]

# =========================
# IDEA GENERATION
# =========================

def generate_idea():
    return {
        "name": "AI Landing Page Builder",
        "description": "Generate landing pages automatically using templates and AI",
        "type": "web_product"
    }

# =========================
# DESIGN PACKAGE
# =========================

def build_design(idea):
    path = os.path.join(OUT, idea["name"].replace(" ","_") + "_design.md")
    content = f"# Design\n\nProduct: {idea['name']}\n\nModern UI, dark mode, dashboard + builder"
    open(path,"w",encoding="utf-8").write(content)
    return path

# =========================
# CODE PACKAGE
# =========================

def build_code(idea):
    path = os.path.join(OUT, idea["name"].replace(" ","_") + "_code.md")
    content = f"# Code Plan\n\nStack: FastAPI + React\n\nFeature: {idea['description']}"
    open(path,"w",encoding="utf-8").write(content)
    return path

# =========================
# GITHUB PLAN
# =========================

def build_pr_plan(idea):
    path = os.path.join(OUT, idea["name"].replace(" ","_") + "_pr.md")
    content = f"# PR PLAN\n\nCreate branch and push {idea['name']}"
    open(path,"w",encoding="utf-8").write(content)
    return path

# =========================
# TASK BUILD
# =========================

def create_task(idea, design, code, pr):
    return {
        "id": new_id(),
        "title": f"FULL CREATOR: {idea['name']}",
        "details": idea["description"],
        "priority": 10,
        "risk": "medium",
        "lane": "full_creator",
        "status": "pending",
        "created_at": now(),
        "executor": "gateway_plan_v7_2",
        "evidence_required": True,
        "gateway_plan": [
            {"tool":"file_write","args":{"path":design}},
            {"tool":"file_write","args":{"path":code}},
            {"tool":"file_write","args":{"path":pr}},
            {"tool":"md_report","args":{
                "path": f"jarvis_stage3_artifacts/full_creator_v8_8/reports/{idea['name']}.md",
                "title": idea["name"],
                "body": "Full creator pipeline generated"
            }}
        ]
    }

# =========================
# MAIN
# =========================

def run(limit=2):
    q = load_queue()

    created = []
    for _ in range(limit):
        idea = generate_idea()
        design = build_design(idea)
        code = build_code(idea)
        pr = build_pr_plan(idea)

        task = create_task(idea, design, code, pr)
        q["items"].append(task)
        created.append(task["id"])

    q["updated_at"] = now()
    save_queue(q)

    report = {
        "created": created,
        "queue_size": len(q["items"]),
        "time": now()
    }

    out = os.path.join(OUT, "latest_full_creator_report.json")
    open(out,"w",encoding="utf-8").write(json.dumps(report,indent=2))

    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    run()