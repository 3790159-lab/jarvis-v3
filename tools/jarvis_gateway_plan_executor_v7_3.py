import argparse, json, importlib.util
from pathlib import Path
from datetime import datetime

def now():
    return datetime.now().isoformat(timespec="seconds")

def read_json(p, d):
    try:
        p=Path(p)
        return json.loads(p.read_text()) if p.exists() else d
    except:
        return d

def write_json(p, data):
    p=Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def load_gateway(root):
    p=Path(root)/"tools/jarvis_unified_tool_gateway_v7_0.py"
    spec=importlib.util.spec_from_file_location("gw", str(p))
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def safe_call(gateway, root, tool, args, retries=2):
    for i in range(retries+1):
        res = gateway.invoke_tool(root, root+"/jarvis_stage3_artifacts/unified_tool_gateway_v7_0", tool, args)
        if res.get("ok"):
            return res, i
    return res, retries

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=8)
    args=ap.parse_args()

    root=args.project_root
    out=Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    queue_path=Path(root)/"state/jarvis_brain/action_queue_v6_4.json"
    queue=read_json(queue_path, {"items":[]})

    gateway=load_gateway(root)

    run_id="v7_3_exec_"+datetime.now().strftime("%H%M%S")
    run_dir=out/run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    tasks=[t for t in queue["items"] if t.get("status")=="pending" and t.get("executor")=="gateway_plan_v7_2"]
    tasks=tasks[:args.limit]

    completed=[]
    failed=[]

    for task in tasks:
        tid=task["id"]
        tdir=run_dir/tid
        tdir.mkdir()

        task["status"]="running"

        ok=True
        step_results=[]

        for i, step in enumerate(task.get("gateway_plan", [])):
            tool=step["tool"]
            args=step.get("args",{})

            res, retry_count = safe_call(gateway, root, tool, args)

            step_results.append({
                "tool":tool,
                "ok":res.get("ok"),
                "retries":retry_count
            })

            write_json(tdir/f"step_{i}.json", res)

            # 🔥 fallback логика
            if not res.get("ok"):
                if tool == "code_metrics":
                    fallback = gateway.invoke_tool(root, root+"/jarvis_stage3_artifacts/unified_tool_gateway_v7_0", "python_syntax_scan", {"path":"app"})
                    if fallback.get("ok"):
                        continue
                ok=False
                break

        if ok:
            task["status"]="completed"
            completed.append(tid)
        else:
            task["status"]="failed"
            failed.append(tid)

        task["updated_at"]=now()

    write_json(queue_path, queue)

    report={
        "executed":len(tasks),
        "completed":completed,
        "failed":failed,
        "run_dir":str(run_dir)
    }

    write_json(out/"latest_v7_3_report.json", report)
    print(json.dumps(report, indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())