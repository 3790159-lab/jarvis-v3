$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe -m py_compile `
        .\app\main.py `
        .\app\models\auto_memory.py `
        .\app\services\memory\memory_analysis_service.py `
        .\app\services\memory\auto_memory_pipeline.py `
        .\app\api\mission_memory_bridge.py
} else {
    python -m py_compile `
        .\app\main.py `
        .\app\models\auto_memory.py `
        .\app\services\memory\memory_analysis_service.py `
        .\app\services\memory\auto_memory_pipeline.py `
        .\app\api\mission_memory_bridge.py
}
