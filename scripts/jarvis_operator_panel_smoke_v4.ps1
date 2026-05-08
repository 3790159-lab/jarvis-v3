﻿
param(
    [string]$PanelUrl = "http://127.0.0.1:8028"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Utf8JsonPost {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][hashtable]$Payload
    )
    $json = $Payload | ConvertTo-Json -Depth 20 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Method POST -Uri $Uri -ContentType "application/json; charset=utf-8" -Body $bytes -TimeoutSec 40
}

$paths = @(
    "/",
    "/api/meta",
    "/api/probe/summary",
    "/api/hardening",
    "/api/runs",
    "/api/state"
)

foreach ($p in $paths) {
    $uri = $PanelUrl.TrimEnd("/") + $p
    Write-Host "GET $uri" -ForegroundColor Cyan
    try {
        if ($p -eq "/") {
            (Invoke-WebRequest -Method Get -Uri $uri -TimeoutSec 15).StatusCode
        } else {
            Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 20 | ConvertTo-Json -Depth 20
        }
    } catch {
        Write-Warning $_.Exception.Message
    }
    Write-Host ""
}

$chatTests = @(
    "Привет, куда ты сохраняешь данные ?",
    "Окей, как тебя зовут ?",
    "Какие сейчас доступны сервисы и ии агенты ?"
)

foreach ($msg in $chatTests) {
    Write-Host "POST /api/chat => $msg" -ForegroundColor Green
    try {
        Invoke-Utf8JsonPost -Uri ($PanelUrl.TrimEnd("/") + "/api/chat") -Payload @{
            message = $msg
            mode    = "default"
        } | ConvertTo-Json -Depth 20
    } catch {
        Write-Warning $_.Exception.Message
    }
    Write-Host ""
}

Write-Host "POST /api/task => backend status task" -ForegroundColor Green
try {
    Invoke-Utf8JsonPost -Uri ($PanelUrl.TrimEnd("/") + "/api/task") -Payload @{
        objective   = "Проверь состояние backend, health и доступные агенты, потом дай краткий операторский отчёт."
        mode        = "task"
        constraints = "Используй только локальные endpoints`nНе делай destructive actions"
    } | ConvertTo-Json -Depth 20
} catch {
    Write-Warning $_.Exception.Message
}
