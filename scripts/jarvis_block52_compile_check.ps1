$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe -m py_compile `
        .\app\main.py `
        .\app\models\mission_run_memory.py `
        .\app\services\memory\mission_run_memory_hook.py `
        .\app\services\memory\mission_auto_persist_helper.py `
        .\app\api\mission_run_memory.py
} else {
    python -m py_compile `
        .\app\main.py `
        .\app\models\mission_run_memory.py `
        .\app\services\memory\mission_run_memory_hook.py `
        .\app\services\memory\mission_auto_persist_helper.py `
        .\app\api\mission_run_memory.py
}
