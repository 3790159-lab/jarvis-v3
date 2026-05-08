@echo off
cd /d "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_jarvis_operator_panel_safe_v4.ps1" -ProjectRoot "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" -BackendBaseUrl "http://127.0.0.1:8015" -PanelHost "127.0.0.1" -PanelPort 8028 -StopExistingOnPort -OpenBrowser -RunSmoke
