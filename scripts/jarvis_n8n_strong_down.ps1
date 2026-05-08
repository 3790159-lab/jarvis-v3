param(
    [string]$StackDir = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\n8n_docker_strong"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location -Path $StackDir
docker compose down