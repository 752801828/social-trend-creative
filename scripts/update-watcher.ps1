[CmdletBinding()]
param([int]$PollSeconds = 2)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $ProjectRoot "data"
$RequestPath = Join-Path $DataDir "update-request.json"
$StatusPath = Join-Path $DataDir "update-status.json"
$LogPath = Join-Path $DataDir "update-watcher.log"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

function Write-UpdateStatus([hashtable]$Status) {
    $temporary = "$StatusPath.tmp"
    $json = $Status | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    Move-Item -Force -LiteralPath $temporary -Destination $StatusPath
}

function Invoke-LoggedCommand([scriptblock]$Command) {
    $output = & $Command 2>&1
    $output | Out-File -FilePath $LogPath -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        throw ($output -join "`n")
    }
    return $output
}

while ($true) {
    if (Test-Path -LiteralPath $RequestPath) {
        $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding utf8 | ConvertFrom-Json
        try {
            Write-UpdateStatus @{
                request_id = $request.request_id
                status = "running"
                message = "Pulling code and rebuilding the service"
                requested_at = $request.requested_at
                started_at = [DateTime]::UtcNow.ToString("o")
            }

            $branch = (Invoke-LoggedCommand { git -C $ProjectRoot branch --show-current } | Select-Object -Last 1).Trim()
            if ($branch -ne "main") {
                throw "The deployment repository is on branch $branch; switch it to main before updating"
            }
            $changes = & git -C $ProjectRoot status --porcelain
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to read the deployment repository status"
            }
            if ($changes) {
                throw "The deployment repository has uncommitted changes; automatic update stopped"
            }

            Invoke-LoggedCommand { git -C $ProjectRoot pull --ff-only origin main } | Out-Null
            Invoke-LoggedCommand { docker compose --project-directory $ProjectRoot up -d --build social-trend-creative } | Out-Null

            $healthy = $false
            for ($attempt = 0; $attempt -lt 60; $attempt++) {
                try {
                    $health = Invoke-RestMethod -Uri "http://127.0.0.1:5920/health" -TimeoutSec 3
                    if ($health.status -eq "ok") {
                        $healthy = $true
                        break
                    }
                } catch {
                    Start-Sleep -Seconds 2
                }
            }
            if (-not $healthy) {
                throw "The container was rebuilt but did not become healthy within 120 seconds"
            }

            $commit = (Invoke-LoggedCommand { git -C $ProjectRoot rev-parse --short HEAD } | Select-Object -Last 1).Trim()
            Write-UpdateStatus @{
                request_id = $request.request_id
                status = "succeeded"
                message = "Project updated and restarted"
                commit = $commit
                requested_at = $request.requested_at
                completed_at = [DateTime]::UtcNow.ToString("o")
            }
        } catch {
            $_ | Out-File -FilePath $LogPath -Append -Encoding utf8
            Write-UpdateStatus @{
                request_id = $request.request_id
                status = "failed"
                message = $_.Exception.Message
                requested_at = $request.requested_at
                completed_at = [DateTime]::UtcNow.ToString("o")
            }
        } finally {
            Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds $PollSeconds
}
