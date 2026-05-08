from app.agent_bootstrap import ensure_required_agents

if __name__ == "__main__":
    result = ensure_required_agents("state")
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
