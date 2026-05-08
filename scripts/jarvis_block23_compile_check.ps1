param()

$Python = ".\.venv\Scripts\python.exe"

Write-Host "== Block 2.3 compile =="

$Targets = @(
 "app\services\tool_router.py",
 "app\routers\multistep.py"
)

foreach ($t in $Targets) {
    & $Python -m py_compile $t
    if ($LASTEXITCODE -ne 0) { throw "Compile error in $t" }
}

Write-Host "OK"