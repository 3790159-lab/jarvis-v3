from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_brain_foundation import JarvisBrainFoundation

brain = JarvisBrainFoundation(PROJECT_ROOT)
compiled = brain.compile_task(
    "РЎРѕР·РґР°Р№ РґРёРЅР°РјРёС‡РµСЃРєРёР№ n8n pipeline: webhook, РїСЂРѕРІРµСЂРєР° РґР°РЅРЅС‹С…, РІРЅРµС€РЅРёР№ API, Р»РѕРіРёС‡РµСЃРєРѕРµ СЂРµС€РµРЅРёРµ, Telegram РѕС‚С‡С‘С‚ Рё СЃРѕС…СЂР°РЅРµРЅРёРµ СЂРµР·СѓР»СЊС‚Р°С‚Р°"
)
print(brain.summary(compiled))
print(json.dumps(compiled.__dict__, ensure_ascii=False, indent=2, default=str))

assert compiled.intent == "automation_pipeline"
assert "n8n_super_agent" in compiled.tools
assert compiled.selected_provider in {"openai", "anthropic", "ollama", "rule_based", "gemini"}