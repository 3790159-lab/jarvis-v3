from app.runtime_repair import repair_runtime_state

if __name__ == "__main__":
    result = repair_runtime_state("state")
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
