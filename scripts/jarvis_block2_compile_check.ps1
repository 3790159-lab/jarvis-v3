$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe -m py_compile `
        .\app\main.py `
        .\app\api\mission_ai_bridge.py `
        .\app\services\ai\mission_ai_orchestrator.py `
        .\app\services\ai\task_classifier.py `
        .\app\models\mission_ai.py
} else {
    python -m py_compile `
        .\app\main.py `
        .\app\api\mission_ai_bridge.py `
        .\app\services\ai\mission_ai_orchestrator.py `
        .\app\services\ai\task_classifier.py `
        .\app\models\mission_ai.py
}
