param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$Topic = "Jarvis Real Task Execution",
    [string]$PackageName = "",
    [string]$ProjectRootOverride = ""
)

$ErrorActionPreference = "Stop"

$ThisScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ThisScriptDir "jarvis_real_run_helpers.ps1")

$ProjectRoot = Resolve-ProjectRoot -FallbackPath $ProjectRootOverride
Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($PackageName)) {
    $PackageName = "sop_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}

$MissionId = "real_run_sop_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$PackageDir = Join-Path $ProjectRoot ("jarvis_stage3_artifacts\real_runs\" + $PackageName)
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

$ClaudeDraftPath = Join-Path $PackageDir "claude_draft.md"
$OpenAiReviewPath = Join-Path $PackageDir "openai_review.md"
$FinalSopPath = Join-Path $PackageDir "final_sop.md"
$PackageReportPath = Join-Path $PackageDir "package_report.txt"

$ClaudePrompt = @"
Return ONLY the final complete operator-ready SOP document in markdown for this topic: $Topic

Requirements:
- operator-focused
- concise but useful
- use markdown headings
- sections:
  1. Purpose
  2. Preconditions
  3. Pre-run checks
  4. Approval and risk control
  5. Execution procedure
  6. Post-run validation
  7. Failure handling
  8. Evidence and artifacts
- include numbered steps where useful
- output ONLY the SOP body in markdown
- DO NOT mention approval, file-write permissions, saving files, or that you will save later
- DO NOT output summaries, explanations, or meta commentary
- start directly with a markdown H1 title for the SOP
- output ONLY the SOP body in markdown
- DO NOT mention approval, file-write permissions, saving files, or that you will save later
- DO NOT output summaries, explanations, or meta commentary
- start directly with a markdown H1 title for the SOP
- output ONLY the SOP body in markdown
- DO NOT mention approval, file-write permissions, saving files, or that you will save later
- DO NOT output summaries, explanations, or meta commentary
- start directly with a markdown H1 title for the SOP
- output ONLY the SOP body in markdown
- DO NOT mention approval, file-write permissions, saving files, or that you 'will save' later
- DO NOT output summaries, explanations, or meta commentary
- start directly with a markdown H1 title for the SOP
"@

Write-Host "== CLAUDE DRAFT ==" -ForegroundColor Cyan
$ClaudeResult = Invoke-AgentWithApproval `
    -BaseUrl $BaseUrl `
    -AdapterName "claude_code_bridge" `
    -Payload @{
        prompt = $ClaudePrompt
        timeout_seconds = 600
        working_directory = $ProjectRoot
    } `
    -RequestedCapabilities @("code","reasoning","external_ai") `
    -RequestedTools @("shell") `
    -MissionId $MissionId `
    -StepId "step_claude_draft" `
    -ApprovalNote "Approved for real SOP draft"

if (-not $ClaudeResult.ok) { throw "Claude draft step failed." }

$ClaudeText = $ClaudeResult.result.output.text
Assert-NonEmptyText -Text $ClaudeText -ErrorMessage "Claude returned empty SOP draft."
Save-Utf8TextFile -Path $ClaudeDraftPath -Text $ClaudeText

$OpenAiPrompt = @"
You are reviewing a draft SOP for this topic. Return ONLY markdown with exactly two top-level sections: '## Review Notes' and '## Final Revised SOP'. Strip any meta-text about approvals, saving files, summaries, or write permissions. Topic: $Topic

Task:
1. Critically review the draft below.
2. Produce TWO sections in markdown:
   - "## Review Notes"
   - "## Final Revised SOP"
3. In Review Notes, give concise, practical improvements.
4. In Final Revised SOP, produce a clean improved SOP in markdown.
5. Keep it operator-focused and concise.
6. If the draft contains any meta-text about file writes, approvals, saving, or summaries, remove it completely.
7. Return ONLY markdown.
6. If the draft contains any meta-text about file writes, approvals, saving, or summaries, remove it completely.
7. Return ONLY markdown.
6. If the draft contains any meta-text about file writes, approvals, saving, or summaries, remove it completely.
7. Return ONLY markdown.
6. If the draft contains any meta-text about file writes, approvals, saving, or summaries, remove it completely.
7. Return ONLY markdown.

Draft SOP:
$ClaudeText
"@

Write-Host "== OPENAI REVIEW ==" -ForegroundColor Cyan
$OpenAiResult = Invoke-AgentWithApproval `
    -BaseUrl $BaseUrl `
    -AdapterName "openai_compatible_http" `
    -Payload @{
        prompt = $OpenAiPrompt
        timeout_seconds = 600
    } `
    -RequestedCapabilities @("chat","external_ai") `
    -RequestedTools @("http") `
    -MissionId $MissionId `
    -StepId "step_openai_review" `
    -ApprovalNote "Approved for real SOP review"

if (-not $OpenAiResult.ok) { throw "OpenAI review step failed." }

$OpenAiText = $OpenAiResult.result.output.text
Assert-NonEmptyText -Text $OpenAiText -ErrorMessage "OpenAI returned empty review."
Save-Utf8TextFile -Path $OpenAiReviewPath -Text $OpenAiText

$ReviewNotes = ""
$FinalRevised = ""

if ($OpenAiText -match '(?s)## Review Notes\s*(.*?)\s*## Final Revised SOP\s*(.*)$') {
    $ReviewNotes = $matches[1].Trim()
    $FinalRevised = $matches[2].Trim()
} else {
    $ReviewNotes = "OpenAI response did not match the exact two-section template, so the full response was used as the revised SOP."
    $FinalRevised = $OpenAiText.Trim()
}

Assert-NonEmptyText -Text $FinalRevised -ErrorMessage "Final revised SOP content is empty."

$FinalDocument = @"
# Jarvis Real Task Execution SOP

> Generated via real operator workflow.
> Mission ID: $MissionId
> Topic: $Topic

## Final SOP
$FinalRevised

---

## Review Summary
$ReviewNotes

---

## Artifact Paths
- Claude draft: $ClaudeDraftPath
- OpenAI review: $OpenAiReviewPath
- Final SOP: $FinalSopPath
- Package report: $PackageReportPath
"@

Save-Utf8TextFile -Path $FinalSopPath -Text $FinalDocument

$ClaudeChars = $ClaudeText.Length
$OpenAiChars = $OpenAiText.Length
$FinalChars = $FinalDocument.Length

$PackageReport = @"
Real SOP Package Report
Mission ID: $MissionId
Topic: $Topic

Artifacts:
- Claude draft: $ClaudeDraftPath
- OpenAI review: $OpenAiReviewPath
- Final SOP: $FinalSopPath

Metrics:
- Claude chars: $ClaudeChars
- OpenAI chars: $OpenAiChars
- Final chars: $FinalChars

Checks:
- Claude draft present: $(Test-Path $ClaudeDraftPath)
- OpenAI review present: $(Test-Path $OpenAiReviewPath)
- Final SOP present: $(Test-Path $FinalSopPath)

Preview:
$($FinalDocument -split "`r?`n" | Select-Object -First 14 | Out-String)
"@

Save-Utf8TextFile -Path $PackageReportPath -Text $PackageReport

$Memory = Invoke-GovernedJsonRequest -Method "POST" -Uri "$BaseUrl/api/memory/remember" -Body @{
    kind = "real_run_result"
    title = "Real SOP package - $Topic"
    text_body = $PackageReport
    tags = @("real_run","sop","claude","openai","package")
    artifact_path = $FinalSopPath
    mission_id = $MissionId
    source = "jarvis_run_real_sop_package"
}

$Search = Invoke-GovernedJsonRequest -Method "GET" -Uri "$BaseUrl/api/memory/search?q=sop&limit=10"

Write-Host "== REAL RUN RESULT ==" -ForegroundColor Green
[pscustomobject]@{
    mission_id = $MissionId
    topic = $Topic
    claude_draft = $ClaudeDraftPath
    openai_review = $OpenAiReviewPath
    final_sop = $FinalSopPath
    package_report = $PackageReportPath
    memory_ok = ($Memory.ok -eq $true)
    search_hits = $Search.count
} | Format-List


