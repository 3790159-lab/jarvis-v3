import subprocess, json
from pathlib import Path
from datetime import datetime

def now():
    return datetime.now().isoformat(timespec="seconds")

def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {"ok": r.returncode == 0, "out": r.stdout[-500:], "err": r.stderr[-500:]}
    except Exception as e:
        return {"ok": False, "err": str(e)}

def check_backend():
    import requests
    try:
        r = requests.get("http://127.0.0.1:8015/health", timeout=3)
        return r.status_code == 200
    except:
        return False

def restart_backend():
    return run(["powershell","-ExecutionPolicy","Bypass","-File","scripts\\restart_backend.ps1"])

def main():
    report = {"time": now(), "actions": []}

    # 1. Проверка backend
    if not check_backend():
        r = restart_backend()
        report["actions"].append({"action":"restart_backend","result":r})

    # 2. Compile check tools
    for p in Path("tools").glob("*.py"):
        r = run(["python","-m","py_compile", str(p)])
        if not r["ok"]:
            report["actions"].append({"action":"fix_compile","file":str(p),"result":r})

    # 3. Проверка queue файла
    qp = Path("state/jarvis_brain/action_queue_v6_4.json")
    if not qp.exists():
        report["actions"].append({"action":"queue_missing"})

    Path("jarvis_stage3_artifacts/self_healing_v7_8/latest.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()