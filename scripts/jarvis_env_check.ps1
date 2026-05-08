$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

$Code = @"
from app.core.env_bootstrap import bootstrap_env
import os

loaded = bootstrap_env()
print("ENV loaded from:", loaded)
print("OPENAI_API_KEY visible:", bool(os.getenv("OPENAI_API_KEY")))
print("ANTHROPIC_API_KEY visible:", bool(os.getenv("ANTHROPIC_API_KEY")))
print("AI_ROUTER_ENABLE_OPENAI:", os.getenv("AI_ROUTER_ENABLE_OPENAI"))
print("AI_ROUTER_ENABLE_ANTHROPIC:", os.getenv("AI_ROUTER_ENABLE_ANTHROPIC"))
"@

if (Test-Path ".\.venv\Scripts\python.exe") {
    $Code | .\.venv\Scripts\python.exe
} else {
    $Code | python
}
