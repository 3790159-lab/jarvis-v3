import json

def enhance_prompt(prompt):
    return f"{prompt}, ultra realistic, high detail, cinematic lighting, professional photography, perfect anatomy"

def choose_provider(payload):
    if payload.get("quality_target") == "high":
        return "premium"
    return "default"

def process(payload):
    prompt = payload.get("prompt", "")
    enhanced = enhance_prompt(prompt)

    provider = choose_provider(payload)

    return {
        "enhanced_prompt": enhanced,
        "provider": provider,
        "original": payload
    }

if __name__ == "__main__":
    import sys
    data = json.loads(sys.stdin.read())
    print(json.dumps(process(data)))