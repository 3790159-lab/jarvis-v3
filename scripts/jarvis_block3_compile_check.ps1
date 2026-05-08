$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe -m py_compile `
        .\app\main.py `
        .\app\models\specialized_agents.py `
        .\app\services\ai\specialized_prompt_builder.py `
        .\app\services\ai\specialized_agent_service.py `
        .\app\services\ai\mission_ai_orchestrator.py `
        .\app\api\specialized_agents.py
} else {
    python -m py_compile `
        .\app\main.py `
        .\app\models\specialized_agents.py `
        .\app\services\ai\specialized_prompt_builder.py `
        .\app\services\ai\specialized_agent_service.py `
        .\app\services\ai\mission_ai_orchestrator.py `
        .\app\api\specialized_agents.py
}
