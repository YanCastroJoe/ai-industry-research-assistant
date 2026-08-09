[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

$ErrorActionPreference = "Stop"

function Invoke-JsonPost {
    param(
        [string]$Uri,
        [hashtable]$Body
    )

    $json = $Body | ConvertTo-Json -Depth 8
    return Invoke-RestMethod -Method Post -Uri $Uri -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($json))
}

$health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 5
if ($health.status -ne "ok") {
    throw "Health check failed."
}

$sessionId = "demo-smoke-$(Get-Date -Format 'yyyyMMddHHmmss')"
Invoke-JsonPost -Uri "$BaseUrl/api/docflow/memories" -Body @{
    session_id = $sessionId
    memory_key = "demo preference"
    content = "Keep output concise and prioritize risks, owners, and deadlines."
} | Out-Null

$task = Invoke-JsonPost -Uri "$BaseUrl/api/docflow/tasks" -Body @{
    title = "Automated demo check"
    session_id = $sessionId
    goal = "Create a weekly report, a risk table, and three slides; cite every conclusion."
    text = "The team completed the V3 knowledge-base upgrade on August 5 and all 30 regression cases passed. A missing document-context variable was found in the custom prompt and the default RAG template was restored. The next action is to add failure cases before August 12. The main risk is model API instability, so timeout, retry, and fallback controls are planned."
}

if ($task.status -ne "awaiting_review") {
    throw "The Agent did not reach awaiting_review. Current status: $($task.status)"
}
if (-not $task.result.verification.passed) {
    throw "Citation verification failed."
}
if ($task.result.metrics.executed_steps -lt 1) {
    throw "No tool execution steps were recorded."
}

$review = Invoke-JsonPost -Uri "$BaseUrl/api/docflow/tasks/$($task.id)/review" -Body @{
    action = "approve"
    note = "Automated demo check passed"
}
if ($review.status -ne "approved") {
    throw "The review endpoint did not return approved."
}

$exported = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/docflow/tasks/$($task.id)/export" -TimeoutSec 10
if ($exported.StatusCode -ne 200 -or $exported.Content.Length -lt 20) {
    throw "Markdown export validation failed."
}

Write-Host "[PASS] DocFlow demo flow is ready."
Write-Host "       Health -> Session Memory -> Planner/Runtime -> citation verification -> review -> export"
Write-Host "       Task ID: $($task.id); steps: $($task.result.metrics.executed_steps); elapsed: $($task.result.metrics.elapsed_ms) ms"
