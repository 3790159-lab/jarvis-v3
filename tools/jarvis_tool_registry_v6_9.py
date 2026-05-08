import argparse, json, os, re, shutil, socket, subprocess, time, hashlib, sqlite3
from pathlib import Path
from datetime import datetime


def now():
    return datetime.now().isoformat(timespec="seconds")


def safe_path(root, rel):
    root = Path(root).resolve()
    p = (root / rel).resolve()
    if not str(p).startswith(str(root)):
        raise ValueError("Path escapes project root")
    return p


def read_text(p):
    return Path(p).read_text(encoding="utf-8", errors="replace")


def write_text(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def read_json(p, default=None):
    try:
        p = Path(p)
        if not p.exists():
            return default
        return json.loads(read_text(p))
    except Exception as e:
        return {"_error": str(e)}


def write_json(p, data):
    write_text(p, json.dumps(data, ensure_ascii=False, indent=2))


def run_cmd(cmd, cwd, timeout=90):
    try:
        r = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout, shell=False)
        return {"ok": r.returncode == 0, "returncode": r.returncode, "stdout": r.stdout[-12000:], "stderr": r.stderr[-12000:], "cmd": cmd}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "cmd": cmd}


def tool_echo(root,args): return {"ok": True, "echo": args}
def tool_timestamp(root,args): return {"ok": True, "time": now()}
def tool_project_tree(root,args):
    base=safe_path(root,args.get("path","."))
    limit=int(args.get("limit",200))
    out=[]
    for i,p in enumerate(base.rglob("*")):
        if i>=limit: break
        out.append({"path":str(p.relative_to(root)),"kind":"dir" if p.is_dir() else "file"})
    return {"ok":True,"items":out}
def tool_find_files(root,args):
    pattern=args.get("pattern","*")
    base=safe_path(root,args.get("path","."))
    return {"ok":True,"files":[str(p.relative_to(root)) for p in base.rglob(pattern) if p.is_file()][:int(args.get("limit",100))]}
def tool_find_dirs(root,args):
    pattern=args.get("pattern","*")
    base=safe_path(root,args.get("path","."))
    return {"ok":True,"dirs":[str(p.relative_to(root)) for p in base.rglob(pattern) if p.is_dir()][:int(args.get("limit",100))]}
def tool_file_stats(root,args):
    p=safe_path(root,args["path"])
    if not p.exists(): return {"ok":False,"error":"missing"}
    st=p.stat()
    return {"ok":True,"path":str(p),"size":st.st_size,"mtime":datetime.fromtimestamp(st.st_mtime).isoformat(),"is_file":p.is_file(),"is_dir":p.is_dir()}
def tool_count_lines(root,args):
    p=safe_path(root,args["path"])
    if not p.exists(): return {"ok":False,"error":"missing"}
    return {"ok":True,"lines":len(read_text(p).splitlines())}
def tool_read_head(root,args):
    p=safe_path(root,args["path"]); n=int(args.get("lines",40))
    return {"ok":p.exists(),"head":read_text(p).splitlines()[:n] if p.exists() else []}
def tool_read_tail(root,args):
    p=safe_path(root,args["path"]); n=int(args.get("lines",40))
    return {"ok":p.exists(),"tail":read_text(p).splitlines()[-n:] if p.exists() else []}
def tool_touch_file(root,args):
    p=safe_path(root,args["path"]); p.parent.mkdir(parents=True,exist_ok=True); p.touch()
    return {"ok":True,"path":str(p)}
def tool_md_report(root,args):
    p=safe_path(root,args.get("path","jarvis_stage3_artifacts/reports_v6_9/report.md"))
    write_text(p, "# "+args.get("title","Jarvis Report")+"\n\nCreated: `"+now()+"`\n\n"+args.get("body","")+"\n")
    return {"ok":True,"path":str(p)}
def tool_json_schema_keys(root,args):
    p=safe_path(root,args["path"]); data=read_json(p,{})
    return {"ok":True,"keys":list(data.keys()) if isinstance(data,dict) else [],"type":type(data).__name__}
def tool_json_get(root,args):
    data=read_json(safe_path(root,args["path"]),{})
    cur=data
    for part in args.get("key","").split("."):
        if not part: continue
        cur=cur.get(part) if isinstance(cur,dict) else None
    return {"ok":True,"value":cur}
def tool_json_set(root,args):
    p=safe_path(root,args["path"]); data=read_json(p,{})
    if not isinstance(data,dict): data={}
    data[args["key"]]=args.get("value")
    write_json(p,data)
    return {"ok":True,"path":str(p)}
def tool_sha1_file(root,args):
    p=safe_path(root,args["path"])
    return {"ok":p.exists(),"sha1":hashlib.sha1(p.read_bytes()).hexdigest() if p.exists() else None}
def tool_md_index(root,args):
    base=safe_path(root,args.get("path","."))
    files=[str(p.relative_to(root)) for p in base.rglob("*.md") if p.is_file()][:int(args.get("limit",100))]
    return {"ok":True,"markdown_files":files}
def tool_python_syntax_scan(root,args):
    base=safe_path(root,args.get("path","app"))
    files=list(base.rglob("*.py"))[:int(args.get("limit",200))]
    results=[]
    for f in files:
        r=run_cmd(["python","-m","py_compile",str(f)],root,60)
        results.append({"file":str(f.relative_to(root)),"ok":r["ok"],"stderr":r.get("stderr","")[-1000:]})
    return {"ok":all(x["ok"] for x in results),"results":results}
def tool_ps_syntax_scan(root,args):
    base=safe_path(root,args.get("path","scripts"))
    files=list(base.rglob("*.ps1"))[:int(args.get("limit",100))]
    results=[]
    for f in files:
        cmd=["powershell","-NoProfile","-Command",f"$null=[System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw '{str(f)}'), [ref]$null); 'OK'"]
        r=run_cmd(cmd,root,60)
        results.append({"file":str(f.relative_to(root)),"ok":r["ok"]})
    return {"ok":all(x["ok"] for x in results),"results":results}
def tool_import_map(root,args):
    base=safe_path(root,args.get("path","app")); imports={}
    for f in list(base.rglob("*.py"))[:200]:
        found=[]
        for line in read_text(f).splitlines():
            if line.startswith("import ") or line.startswith("from "): found.append(line.strip())
        imports[str(f.relative_to(root))]=found[:30]
    return {"ok":True,"imports":imports}
def tool_todo_scan(root,args):
    base=safe_path(root,args.get("path","."))
    out=[]
    for f in list(base.rglob("*"))[:5000]:
        if f.is_file() and f.suffix.lower() in [".py",".ps1",".md",".txt",".json"]:
            text=read_text(f)
            if "TODO" in text or "FIXME" in text:
                out.append(str(f.relative_to(root)))
    return {"ok":True,"files":out[:100]}
def tool_error_scan(root,args):
    base=safe_path(root,args.get("path","jarvis_stage3_artifacts"))
    out=[]
    for f in list(base.rglob("*"))[:5000]:
        if f.is_file() and f.suffix.lower() in [".log",".txt",".md",".json"]:
            text=read_text(f)
            if re.search(r"error|exception|traceback|failed",text,re.I):
                out.append(str(f.relative_to(root)))
    return {"ok":True,"files":out[:100]}
def tool_route_scan(root,args):
    base=safe_path(root,args.get("path","app"))
    routes=[]
    for f in base.rglob("*.py"):
        text=read_text(f)
        for m in re.finditer(r'@[^.\n]*\.(get|post|put|delete|patch)\(["\']([^"\']+)',text):
            routes.append({"file":str(f.relative_to(root)),"method":m.group(1),"path":m.group(2)})
    return {"ok":True,"routes":routes}
def tool_fastapi_router_probe(root,args):
    return tool_route_scan(root,args)
def tool_openapi_probe(root,args):
    import urllib.request
    url=args.get("base_url","http://127.0.0.1:8015").rstrip()+"/openapi.json"
    try:
        with urllib.request.urlopen(url,timeout=5) as r:
            data=json.loads(r.read(200000).decode("utf-8","replace"))
            return {"ok":True,"paths":list(data.get("paths",{}).keys())[:300]}
    except Exception as e: return {"ok":False,"error":str(e)}
def tool_health_matrix(root,args):
    import urllib.request
    base=args.get("base_url","http://127.0.0.1:8015").rstrip()
    eps=args.get("endpoints",["/","/health","/api/ai/health","/api/autonomy/health"])
    res=[]
    for ep in eps:
        try:
            with urllib.request.urlopen(base+ep,timeout=5) as r: res.append({"endpoint":ep,"ok":200<=r.status<400,"status":r.status})
        except Exception as e: res.append({"endpoint":ep,"ok":False,"error":str(e)})
    return {"ok":any(x["ok"] for x in res),"results":res}
def tool_latency_probe(root,args):
    import urllib.request
    url=args.get("url","http://127.0.0.1:8015/health")
    t=time.time()
    try:
        with urllib.request.urlopen(url,timeout=5) as r: pass
        return {"ok":True,"ms":int((time.time()-t)*1000)}
    except Exception as e: return {"ok":False,"ms":int((time.time()-t)*1000),"error":str(e)}
def tool_port_wait(root,args):
    host=args.get("host","127.0.0.1"); port=int(args["port"]); timeout=int(args.get("timeout",20)); start=time.time()
    while time.time()-start<timeout:
        try:
            with socket.create_connection((host,port),timeout=1): return {"ok":True,"open":True}
        except Exception: time.sleep(1)
    return {"ok":False,"open":False}
def tool_http_download_text(root,args):
    import urllib.request
    url=args["url"]; p=safe_path(root,args["path"])
    with urllib.request.urlopen(url,timeout=15) as r: text=r.read(500000).decode("utf-8","replace")
    write_text(p,text)
    return {"ok":True,"path":str(p),"bytes":len(text.encode())}
def tool_n8n_health_matrix(root,args):
    return tool_health_matrix(root,{"base_url":args.get("base_url","http://127.0.0.1:5678"),"endpoints":["/healthz","/rest/settings"]})
def tool_n8n_workflow_stub(root,args):
    p=safe_path(root,args.get("path","jarvis_stage3_artifacts/n8n_workflows_v6_9/workflow_stub.json"))
    data={"name":args.get("name","Jarvis Stub Workflow"),"nodes":[],"connections":{},"created_at":now()}
    write_json(p,data); return {"ok":True,"path":str(p)}
def tool_env_snapshot(root,args):
    names=args.get("names",["APP_PORT","BACKEND_BASE_URL","LLM_MODE","LLM_MODEL"])
    return {"ok":True,"env":{n:bool(os.environ.get(n)) for n in names}}
def tool_path_check(root,args):
    paths=args.get("paths",["app","scripts","tools","state"])
    return {"ok":True,"paths":{p:safe_path(root,p).exists() for p in paths}}
def tool_process_by_port(root,args):
    port=str(args["port"])
    return run_cmd(["powershell","-NoProfile","-Command",f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,State,OwningProcess | ConvertTo-Json -Depth 3"],root)
def tool_kill_process_by_port_plan(root,args):
    port=str(args["port"])
    return {"ok":True,"plan":f"Operator review required before stopping process on port {port}. Use existing restart scripts if approved."}
def tool_restart_backend_plan(root,args):
    return {"ok":True,"script":"scripts/restart_backend.ps1","note":"Plan only; real restart must be called explicitly by operator or safe runner."}
def tool_memory_write_summary(root,args):
    p=safe_path(root,"artifacts/memory/summaries/"+args.get("name","summary_"+datetime.now().strftime("%Y%m%d_%H%M%S")+".md"))
    write_text(p,"# "+args.get("title","Jarvis Memory Summary")+"\n\n"+args.get("body","")+"\n")
    return {"ok":True,"path":str(p)}
def tool_memory_index(root,args):
    base=safe_path(root,"artifacts/memory")
    return {"ok":True,"files":[str(p.relative_to(root)) for p in base.rglob("*") if p.is_file()][:200] if base.exists() else []}
def tool_memory_search(root,args):
    base=safe_path(root,"artifacts/memory"); q=args["query"].lower(); res=[]
    if base.exists():
        for p in base.rglob("*"):
            if p.is_file() and q in read_text(p).lower(): res.append(str(p.relative_to(root)))
    return {"ok":True,"results":res[:100]}
def tool_queue_summary(root,args):
    q=read_json(safe_path(root,"state/jarvis_brain/action_queue_v6_4.json"),{"items":[]})
    return {"ok":True,"total":len(q.get("items",[])),"by_status":{s:len([x for x in q.get("items",[]) if x.get("status")==s]) for s in ["pending","running","completed","failed","blocked"]}}
def tool_queue_ready(root,args):
    q=read_json(safe_path(root,"state/jarvis_brain/action_queue_v6_4.json"),{"items":[]})
    ready=[x for x in q.get("items",[]) if x.get("status")=="pending"]
    ready=sorted(ready,key=lambda x:x.get("priority",50))
    return {"ok":True,"ready":ready[:int(args.get("limit",20))]}
def tool_queue_mark_review(root,args):
    qpath=safe_path(root,"state/jarvis_brain/action_queue_v6_4.json"); q=read_json(qpath,{"items":[]})
    for x in q.get("items",[]):
        if x.get("id")==args["id"]:
            x["status"]="blocked"; x["review_required"]=True; x["updated_at"]=now()
            write_json(qpath,q); return {"ok":True,"id":args["id"]}
    return {"ok":False,"error":"not found"}
def tool_artifact_index(root,args):
    base=safe_path(root,args.get("path","jarvis_stage3_artifacts"))
    files=[]
    if base.exists():
        for p in base.rglob("*"):
            if p.is_file(): files.append({"path":str(p.relative_to(root)),"size":p.stat().st_size})
    return {"ok":True,"files":files[:int(args.get("limit",300))]}
def tool_latest_artifacts(root,args):
    base=safe_path(root,args.get("path","jarvis_stage3_artifacts")); files=[]
    if base.exists():
        for p in base.rglob("*"):
            if p.is_file(): files.append((p.stat().st_mtime,p))
    files.sort(reverse=True)
    return {"ok":True,"files":[str(p.relative_to(root)) for _,p in files[:int(args.get("limit",50))]]}
def tool_report_bundle(root,args):
    src=safe_path(root,args.get("src","jarvis_stage3_artifacts")); dst=safe_path(root,args.get("dst","jarvis_stage3_artifacts/report_bundle_v6_9.zip"))
    dst.parent.mkdir(parents=True,exist_ok=True)
    import zipfile
    with zipfile.ZipFile(dst,"w",zipfile.ZIP_DEFLATED) as z:
        for p in list(src.rglob("*"))[:3000]:
            if p.is_file(): z.write(p,p.relative_to(src))
    return {"ok":True,"zip":str(dst)}
def tool_code_metrics(root,args):
    base=safe_path(root,args.get("path","app")); total_files=0; total_lines=0
    for p in base.rglob("*.py"):
        total_files+=1; total_lines+=len(read_text(p).splitlines())
    return {"ok":True,"py_files":total_files,"py_lines":total_lines}
def tool_large_files(root,args):
    base=safe_path(root,args.get("path",".")); minb=int(args.get("min_bytes",1000000)); out=[]
    for p in base.rglob("*"):
        if p.is_file() and p.stat().st_size>=minb: out.append({"path":str(p.relative_to(root)),"size":p.stat().st_size})
    return {"ok":True,"files":out[:100]}
def tool_duplicate_names(root,args):
    base=safe_path(root,args.get("path",".")); d={}
    for p in base.rglob("*"):
        if p.is_file(): d.setdefault(p.name,[]).append(str(p.relative_to(root)))
    return {"ok":True,"duplicates":{k:v for k,v in d.items() if len(v)>1}}
def tool_backup_state(root,args):
    src=safe_path(root,args.get("src","state")); dst=safe_path(root,"jarvis_stage3_artifacts/state_backups_v6_9/state_"+datetime.now().strftime("%Y%m%d_%H%M%S"))
    if not src.exists(): return {"ok":False,"error":"state missing"}
    shutil.copytree(src,dst,dirs_exist_ok=True)
    return {"ok":True,"backup":str(dst)}
def tool_backup_scripts(root,args):
    src=safe_path(root,"scripts"); dst=safe_path(root,"jarvis_stage3_artifacts/script_backups_v6_9/scripts_"+datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copytree(src,dst,dirs_exist_ok=True)
    return {"ok":True,"backup":str(dst)}
def tool_sqlite_tables(root,args):
    p=safe_path(root,args["path"])
    con=sqlite3.connect(str(p)); cur=con.cursor(); cur.execute("select name from sqlite_master where type='table'"); rows=[x[0] for x in cur.fetchall()]; con.close()
    return {"ok":True,"tables":rows}
def tool_sqlite_count_rows(root,args):
    p=safe_path(root,args["path"]); table=args["table"]
    con=sqlite3.connect(str(p)); cur=con.cursor(); cur.execute(f"select count(*) from {table}"); n=cur.fetchone()[0]; con.close()
    return {"ok":True,"table":table,"rows":n}
def tool_config_inventory(root,args):
    pats=["*.env","*.json","*.yaml","*.yml","*.toml","*.ini"]; files=[]
    for pat in pats: files += [str(p.relative_to(root)) for p in Path(root).rglob(pat)]
    return {"ok":True,"configs":files[:200]}
def tool_secret_leak_scan(root,args):
    base=safe_path(root,args.get("path",".")); hits=[]
    rg=re.compile(r"(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]{8,}",re.I)
    for p in list(base.rglob("*"))[:5000]:
        if p.is_file() and p.suffix.lower() in [".py",".ps1",".env",".json",".md",".txt",".yaml",".yml"]:
            if rg.search(read_text(p)): hits.append(str(p.relative_to(root)))
    return {"ok":True,"possible_hits":hits[:100]}
def tool_policy_gate(root,args):
    risk=args.get("risk","low")
    return {"ok":True,"allowed":risk in ["low","safe"],"risk":risk,"decision":"allow" if risk in ["low","safe"] else "operator_review"}
def tool_execution_journal_append(root,args):
    p=safe_path(root,"jarvis_stage3_artifacts/execution_journal_v6_9/journal.jsonl")
    row=args.get("row",{}); row.setdefault("time",now())
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("a",encoding="utf-8") as f: f.write(json.dumps(row,ensure_ascii=False)+"\n")
    return {"ok":True,"path":str(p)}
def tool_execution_journal_tail(root,args):
    p=safe_path(root,"jarvis_stage3_artifacts/execution_journal_v6_9/journal.jsonl")
    if not p.exists(): return {"ok":True,"rows":[]}
    rows=[json.loads(x) for x in read_text(p).splitlines()[-int(args.get("lines",20)):] if x.strip()]
    return {"ok":True,"rows":rows}
def tool_smoke_all_previous(root,args):
    scripts=["scripts/jarvis_tool_registry_smoke_v6_7.ps1","scripts/jarvis_tool_registry_smoke_v6_8.ps1"]
    res=[]
    for s in scripts:
        p=safe_path(root,s)
        if p.exists(): res.append(run_cmd(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(p),"-ProjectRoot",str(root)],root,180))
    return {"ok":all(x.get("ok") for x in res),"results":res}
def tool_capability_manifest(root,args):
    manifest={"created_at":now(),"v6_9_tools":sorted(TOOLS.keys()),"count":len(TOOLS),"total_expected_with_previous":100}
    p=safe_path(root,"jarvis_stage3_artifacts/tool_registry_v6_9/capability_manifest.json")
    write_json(p,manifest); return {"ok":True,"path":str(p),"count":len(TOOLS)}


TOOLS = {
"echo":tool_echo,"timestamp":tool_timestamp,"project_tree":tool_project_tree,"find_files":tool_find_files,"find_dirs":tool_find_dirs,
"file_stats":tool_file_stats,"count_lines":tool_count_lines,"read_head":tool_read_head,"read_tail":tool_read_tail,"touch_file":tool_touch_file,
"md_report":tool_md_report,"json_schema_keys":tool_json_schema_keys,"json_get":tool_json_get,"json_set":tool_json_set,"sha1_file":tool_sha1_file,
"md_index":tool_md_index,"python_syntax_scan":tool_python_syntax_scan,"ps_syntax_scan":tool_ps_syntax_scan,"import_map":tool_import_map,"todo_scan":tool_todo_scan,
"error_scan":tool_error_scan,"route_scan":tool_route_scan,"fastapi_router_probe":tool_fastapi_router_probe,"openapi_probe":tool_openapi_probe,"health_matrix":tool_health_matrix,
"latency_probe":tool_latency_probe,"port_wait":tool_port_wait,"http_download_text":tool_http_download_text,"n8n_health_matrix":tool_n8n_health_matrix,"n8n_workflow_stub":tool_n8n_workflow_stub,
"env_snapshot":tool_env_snapshot,"path_check":tool_path_check,"process_by_port":tool_process_by_port,"kill_process_by_port_plan":tool_kill_process_by_port_plan,"restart_backend_plan":tool_restart_backend_plan,
"memory_write_summary":tool_memory_write_summary,"memory_index":tool_memory_index,"memory_search":tool_memory_search,"queue_summary":tool_queue_summary,"queue_ready":tool_queue_ready,
"queue_mark_review":tool_queue_mark_review,"artifact_index":tool_artifact_index,"latest_artifacts":tool_latest_artifacts,"report_bundle":tool_report_bundle,"code_metrics":tool_code_metrics,
"large_files":tool_large_files,"duplicate_names":tool_duplicate_names,"backup_state":tool_backup_state,"backup_scripts":tool_backup_scripts,"sqlite_tables":tool_sqlite_tables,
"sqlite_count_rows":tool_sqlite_count_rows,"config_inventory":tool_config_inventory,"secret_leak_scan":tool_secret_leak_scan,"policy_gate":tool_policy_gate,"execution_journal_append":tool_execution_journal_append,
"execution_journal_tail":tool_execution_journal_tail,"smoke_all_previous":tool_smoke_all_previous,"capability_manifest":tool_capability_manifest
}


def invoke(root, tool, args):
    if tool not in TOOLS:
        return {"ok":False,"error":"unknown tool","available":sorted(TOOLS)}
    try:
        return TOOLS[tool](root,args or {})
    except Exception as e:
        return {"ok":False,"error":f"{type(e).__name__}: {e}"}


def smoke(root,out_dir):
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    tests=[
        ("echo",{"x":1}),("timestamp",{}),("path_check",{}),("project_tree",{"limit":20}),("find_files",{"pattern":"*.py","limit":20}),
        ("file_stats",{"path":"app/main.py"}),("read_head",{"path":"app/main.py","lines":5}),("touch_file",{"path":"jarvis_stage3_artifacts/tool_registry_v6_9/smoke/touch.txt"}),
        ("md_report",{"title":"V6.9 Smoke","body":"ok","path":"jarvis_stage3_artifacts/tool_registry_v6_9/smoke/report.md"}),
        ("json_set",{"path":"jarvis_stage3_artifacts/tool_registry_v6_9/smoke/test.json","key":"ok","value":True}),
        ("json_get",{"path":"jarvis_stage3_artifacts/tool_registry_v6_9/smoke/test.json","key":"ok"}),("sha1_file",{"path":"jarvis_stage3_artifacts/tool_registry_v6_9/smoke/test.json"}),
        ("md_index",{"path":"jarvis_stage3_artifacts","limit":20}),("todo_scan",{"path":"app"}),("route_scan",{"path":"app"}),
        ("health_matrix",{}),("latency_probe",{}),("env_snapshot",{}),("queue_summary",{}),("artifact_index",{"limit":20}),
        ("latest_artifacts",{"limit":20}),("code_metrics",{}),("config_inventory",{}),("policy_gate",{"risk":"low"}),
        ("execution_journal_append",{"row":{"event":"v6_9_smoke"}}),("execution_journal_tail",{"lines":5}),("capability_manifest",{})
    ]
    results=[]
    for name,args in tests:
        res=invoke(root,name,args)
        results.append({"tool":name,"ok":bool(res.get("ok")),"result":res})
    report={"schema":"jarvis.tool_registry.v6_9","created_at":now(),"tools_added":len(TOOLS),"total_with_previous":100,"passed":len([x for x in results if x["ok"]]),"total":len(results),"results":results,"tools":sorted(TOOLS)}
    write_json(out/"latest_tool_registry_v6_9_smoke.json",report)
    lines=["# Jarvis Tool Registry V6.9 Smoke","",f"Tools added: `{len(TOOLS)}`",f"Total with previous: `100`",f"Passed: `{report['passed']}/{report['total']}`",""]
    for r in results: lines.append(("- ✅ " if r["ok"] else "- ❌ ")+f"`{r['tool']}`")
    write_text(out/"latest_tool_registry_v6_9_smoke.md","\n".join(lines)+"\n")
    print(json.dumps({"ok":True,"tools_added":len(TOOLS),"total_with_previous":100,"passed":report["passed"],"total":report["total"],"latest_json":str(out/"latest_tool_registry_v6_9_smoke.json"),"latest_md":str(out/"latest_tool_registry_v6_9_smoke.md")},ensure_ascii=False,indent=2))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--project-root",required=True)
    ap.add_argument("--out-dir",required=True)
    ap.add_argument("--smoke",action="store_true")
    ap.add_argument("--tool")
    ap.add_argument("--args-json",default="{}")
    args=ap.parse_args()
    if args.smoke:
        smoke(args.project_root,args.out_dir); return 0
    res=invoke(args.project_root,args.tool,json.loads(args.args_json))
    print(json.dumps(res,ensure_ascii=False,indent=2))
    return 0 if res.get("ok") else 2


if __name__=="__main__":
    raise SystemExit(main())