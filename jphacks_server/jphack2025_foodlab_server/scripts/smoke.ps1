[CmdletBinding()]
param(
    [string]$BaseUrl = "https://lab.160.16.126.35.sslip.io",
    [string]$ImagePath = ".\Sample Pictures\meat.jpg",
    [switch]$CreateItem = $true,
    [string]$Storage = "local"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$storageNormalized = $Storage.ToLowerInvariant()
if ($storageNormalized -eq 'local') {
    $Storage = 'cool'
}

$timestamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
$reportDir = Join-Path "reports" "smoke-$timestamp"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

function Write-Summary {
    param([string]$Line)
    $summaryPath = Join-Path $reportDir "SUMMARY.txt"
    Add-Content -Path $summaryPath -Value $Line -Encoding utf8
    Write-Output $Line
}

if (-not $env:LAB_API_KEY) {
    throw "LAB_API_KEY is not set in environment."
}
$headers = @{ 'X-Api-Key' = $env:LAB_API_KEY }
$healthUrl = "$BaseUrl/api/v1/healthz"

# 0) Health without key -> expect 401
try {
    Invoke-WebRequest -UseBasicParsing -Method Get -Uri $healthUrl | Out-Null
    throw "Health without key unexpectedly succeeded"
} catch {
    $status = $_.Exception.Response.StatusCode.value__
    Set-Content -Path (Join-Path $reportDir "A_health_unauthorized.txt") -Value "status=$status" -Encoding utf8
    Write-Summary "HEALTH(no-key)=$status"
    if ($status -ne 401) {
        throw "Expected 401 without key, got $status"
    }
}

# 1) Health with key -> expect 200
$healthResp = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $healthUrl -Headers $headers
Set-Content -Path (Join-Path $reportDir "B_health_ok.txt") -Value "status=$($healthResp.StatusCode)" -Encoding utf8
Write-Summary "HEALTH(with-key)=$($healthResp.StatusCode)"
if ($healthResp.StatusCode -ne 200) {
    throw "Health with key != 200"
}

# 2) Ensure image exists (fallback 1x1 PNG)
if (-not (Test-Path -LiteralPath $ImagePath)) {
    $fallback = Join-Path $reportDir "tmp.png"
    $b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    [IO.File]::WriteAllBytes($fallback, [Convert]::FromBase64String($b64))
    $ImagePath = $fallback
}
$resolvedImage = (Resolve-Path -LiteralPath $ImagePath).Path

# 3) Upload
$uploadResponse = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/uploads" -Headers $headers -Form @{
    file    = Get-Item -LiteralPath $resolvedImage
    storage = $Storage
}
$uploadResponse | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $reportDir "C_upload.json") -Encoding utf8
$uploadId = $uploadResponse.upload_id
if (-not $uploadId) {
    throw "upload_id missing"
}
Write-Summary "UPLOAD: upload_id=$uploadId; storage=$Storage"

# 4) Analyze
$analyzeBody = @{ upload_id = $uploadId; storage = $Storage } | ConvertTo-Json -Compress
$analyzeResponse = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/analyze" -Headers $headers -ContentType "application/json" -Body $analyzeBody
$analyzePath = Join-Path $reportDir "D_analyze.json"
$analyzeResponse | ConvertTo-Json -Depth 8 | Set-Content -Path $analyzePath -Encoding utf8
$jobId = $analyzeResponse.job_id
$result = $null
$jobResultPath = $null
if ($jobId) {
    Write-Summary "ANALYZE: job_id=$jobId"
} else {
    Write-Summary "ANALYZE: sync result"
    $jobResultPath = Join-Path $reportDir "E_job_00.json"
    $analyzeResponse | ConvertTo-Json -Depth 8 | Set-Content -Path $jobResultPath -Encoding utf8
    $result = $analyzeResponse
}

# 5) Poll job until done
$deadline = (Get-Date).AddMinutes(2)
$pollIndex = 0
if ($jobId) {
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $pollIndex += 1
        $jobResp = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/v1/jobs/$jobId" -Headers $headers
        $jobResultPath = Join-Path $reportDir ("E_job_{0:D2}.json" -f $pollIndex)
        $jobResp | ConvertTo-Json -Depth 8 | Set-Content -Path $jobResultPath -Encoding utf8
        if ($jobResp.status -eq 'done') {
            $result = $jobResp.result
            break
        }
    }
    if (-not $result) {
        throw "Analyze job timeout or not done"
    }
}

if (-not $jobResultPath) {
    $jobResultPath = Join-Path $reportDir "E_job_00.json"
    $analyzeResponse | ConvertTo-Json -Depth 8 | Set-Content -Path $jobResultPath -Encoding utf8
}

if (-not $result) {
    $result = Get-Content -Path $jobResultPath | ConvertFrom-Json
}

Write-Summary "JOB: status=done; name=$($result.name); class=$($result.class_id); expiry=$($result.expiry); food_id=$($result.food_id)"

# 6) Create item (optional)
if ($CreateItem) {
    $itemBody = @{ upload_id = $uploadId; storage = $Storage } | ConvertTo-Json -Compress
    $itemResponse = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/items" -Headers $headers -ContentType "application/json" -Body $itemBody
    $itemResponse | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $reportDir "F_item.json") -Encoding utf8
    Write-Summary "ITEM: created id=$($itemResponse.id)"
}

Write-Summary "ALL DONE"
exit 0
