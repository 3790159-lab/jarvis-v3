$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe -m py_compile `
        .\app\main.py `
        .\app\models\provider_routing_policy.py `
        .\app\services\ai\provider_routing_policy_service.py `
        .\app\services\ai\external_executor_service.py `
        .\app\api\provider_routing_policy.py
} else {
    python -m py_compile `
        .\app\main.py `
        .\app\models\provider_routing_policy.py `
        .\app\services\ai\provider_routing_policy_service.py `
        .\app\services\ai\external_executor_service.py `
        .\app\api\provider_routing_policy.py
}
