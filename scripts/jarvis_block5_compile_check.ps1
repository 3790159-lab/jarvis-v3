$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe -m py_compile `
        .\app\main.py `
        .\app\models\obsidian_bridge.py `
        .\app\services\memory\obsidian_bridge_service.py `
        .\app\api\obsidian_bridge.py
} else {
    python -m py_compile `
        .\app\main.py `
        .\app\models\obsidian_bridge.py `
        .\app\services\memory\obsidian_bridge_service.py `
        .\app\api\obsidian_bridge.py
}
