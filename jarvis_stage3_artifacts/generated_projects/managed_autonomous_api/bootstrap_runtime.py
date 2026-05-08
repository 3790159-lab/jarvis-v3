from app.runtime_bootstrap import bootstrap_runtime_agents

if __name__ == "__main__":
    import json
    result = bootstrap_runtime_agents("state", "http://127.0.0.1:8010")
    print(json.dumps(result, ensure_ascii=False, indent=2))
