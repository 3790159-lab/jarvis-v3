param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$OutputPath = ".\artifacts\runtime_outputs\tables\jarvis_real_budget.xlsx"

Write-Host "== Spreadsheet health ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/spreadsheets/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Create local workbook ==" -ForegroundColor Cyan
$CreateBody = @{
    payload = @{
        mode = "local_excel"
        action = "create_table"
        path = $OutputPath
        sheet_name = "Budget"
        headers = @("Category","Plan","Fact","Status")
        rows = @(
            @("Marketing",1000,850,"ok"),
            @("Tools",500,620,"over"),
            @("Ads",1200,1200,"ok")
        )
        format_header = $true
    }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/spreadsheets/execute" -ContentType "application/json" -Body $CreateBody | ConvertTo-Json -Depth 10

Write-Host "`n== Update cells ==" -ForegroundColor Cyan
$UpdateBody = @{
    payload = @{
        mode = "local_excel"
        action = "update_cells"
        path = $OutputPath
        sheet_name = "Budget"
        updates = @(
            @{ cell = "D3"; value = "warning" },
            @{ cell = "B5"; value = "Total" },
            @{ cell = "C5"; value = "=SUM(C2:C4)" }
        )
    }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/spreadsheets/execute" -ContentType "application/json" -Body $UpdateBody | ConvertTo-Json -Depth 10

Write-Host "`n== Append rows ==" -ForegroundColor Cyan
$AppendBody = @{
    payload = @{
        mode = "local_excel"
        action = "append_rows"
        path = $OutputPath
        sheet_name = "Budget"
        rows = @(
            @("Reserve",300,150,"ok")
        )
    }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/spreadsheets/execute" -ContentType "application/json" -Body $AppendBody | ConvertTo-Json -Depth 10

Write-Host "`n== Inspect workbook ==" -ForegroundColor Cyan
$InspectBody = @{
    payload = @{
        mode = "local_excel"
        action = "inspect"
        path = $OutputPath
    }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/spreadsheets/execute" -ContentType "application/json" -Body $InspectBody | ConvertTo-Json -Depth 10

Write-Host "`nWorkbook ready at: $OutputPath" -ForegroundColor Green
