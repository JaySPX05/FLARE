<#
.SYNOPSIS
  Quick end-to-end check of a running FLARE stack (docker compose up, or a deployed URL).

.EXAMPLE
  ./infra/docker/smoke-test.ps1                                   # http://localhost:8080
  ./infra/docker/smoke-test.ps1 -BaseUrl https://my-app.example   # any deployment
#>
param([string]$BaseUrl = "http://localhost:8080")

$ErrorActionPreference = "Continue"
$BaseUrl = $BaseUrl.TrimEnd("/")
$failures = 0

function Check {
    param([string]$Name, [scriptblock]$Test)
    try {
        $detail = & $Test
        Write-Host ("  PASS  {0}  {1}" -f $Name, $detail)
    }
    catch {
        Write-Host ("  FAIL  {0}  -> {1}" -f $Name, $_.Exception.Message) -ForegroundColor Red
        $script:failures++
    }
}

Write-Host "Smoke test against $BaseUrl"

Check "nginx is up (/healthz)" {
    $r = Invoke-WebRequest -Uri "$BaseUrl/healthz" -UseBasicParsing -TimeoutSec 20
    if ($r.StatusCode -ne 200) { throw "status $($r.StatusCode)" }
}

Check "dashboard page served (/)" {
    $r = Invoke-WebRequest -Uri "$BaseUrl/" -UseBasicParsing -TimeoutSec 20
    if ($r.Content -notmatch "FLARE") { throw "index.html not returned" }
}

$segments = $null
Check "risk API through nginx (/api/v1/risk)" {
    $r = Invoke-RestMethod -Uri "$BaseUrl/api/v1/risk?ward_id=koramangala&timestamp=2026-09-07T17:45:00" -TimeoutSec 30
    if (-not $r.segments -or $r.segments.Count -eq 0) { throw "no segments returned" }
    $script:segments = $r.segments
    "$($r.segments.Count) segments"
}

Check "flood points (/api/v1/flood-points)" {
    try {
        $r = Invoke-RestMethod -Uri "$BaseUrl/api/v1/flood-points?ward_id=koramangala" -TimeoutSec 30
    }
    catch {
        throw "not available - commit data/processed/flood_points_koramangala.geojson and rebuild the api image"
    }
    "$($r.points.Count) points"
}

Check "routing API through nginx (/routing/)" {
    $r = Invoke-RestMethod -Uri "$BaseUrl/routing/" -TimeoutSec 60
    if ($r.status -ne "ok") { throw "unexpected response" }
}

Check "flood-aware route (/routing/route)" {
    if (-not $script:segments) { throw "skipped: no segments from the risk API" }
    $first = $script:segments[0].geometry[0]
    $lastSeg = $script:segments[$script:segments.Count - 1].geometry
    $last = $lastSeg[$lastSeg.Count - 1]
    $url = "$BaseUrl/routing/route?start_lat=$($first[1])&start_lon=$($first[0])&end_lat=$($last[1])&end_lon=$($last[0])&timestep=2026-09-07T17:45:00"
    $r = Invoke-RestMethod -Uri $url -TimeoutSec 120
    if (-not $r.features -or $r.features.Count -eq 0) { throw "no route features" }
    $pts = ($r.features | ForEach-Object { $_.geometry.coordinates.Count } | Measure-Object -Maximum).Maximum
    "$($r.features.Count) route(s), up to $pts points, safe route available: $($r.meta.safe_route_available)"
}

Write-Host ""
if ($failures -eq 0) { Write-Host "All checks passed." -ForegroundColor Green; exit 0 }
Write-Host "$failures check(s) failed." -ForegroundColor Red
exit 1
