<#
.SYNOPSIS
  Deploy FLARE to Azure Container Apps (works on an Azure for Students subscription).

.DESCRIPTION
  Creates (or updates) three container apps in one environment, pulling the images that
  the "Container images" GitHub Action pushes to ghcr.io:

    flare-api        internal   Risk API
    flare-routing    internal   Routing API
    flare-frontend   PUBLIC     nginx: dashboard + proxy to the two above

  Safe to re-run: existing apps are updated in place.

.EXAMPLE
  ./infra/azure/deploy.ps1                 # deploy, scale to zero when idle (cheapest)
  ./infra/azure/deploy.ps1 -Demo           # keep one replica of each app warm (no cold starts)
  ./infra/azure/deploy.ps1 -Teardown       # delete everything (stops all charges)

.NOTES
  Prerequisites: Azure CLI (`az login` done) and the three ghcr.io packages set to Public.
#>
param(
    [string]$Owner         = "jayspx05",          # GitHub user/org that owns the images (lowercase)
    [string]$Region        = "centralindia",      # must be one of YOUR subscription's allowed regions
    [string]$ResourceGroup = "flare-rg",
    [string]$EnvName       = "flare-env",
    [string]$Tag           = "latest",
    [switch]$Demo,                                # min 1 replica per app (no cold start, uses credit)
    [switch]$Teardown,                            # delete the resource group and exit
    [string]$GhcrUser,                            # only if the ghcr.io packages are PRIVATE
    [string]$GhcrToken                            # GitHub PAT with read:packages
)

# Native commands (az) write warnings to stderr; don't let Windows PowerShell 5.1 turn those into errors.
$ErrorActionPreference = "Continue"

function Invoke-Az {
    $output = & az @args
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed (exit code $LASTEXITCODE)" }
    return $output
}

function Test-AzResource {
    & az @args --output none 2>$null
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------------------------
if ($Teardown) {
    Write-Host "Deleting resource group '$ResourceGroup' (everything inside it)..."
    Invoke-Az group delete --name $ResourceGroup --yes --no-wait | Out-Null
    Write-Host "Deletion started. It finishes in a few minutes."
    return
}

Write-Host "Checking Azure CLI login..."
& az account show --query name --output tsv 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Not logged in to Azure. Run: az login" }

# Container Apps must exist in the region you deploy to (and your student policy must allow it).
Write-Host "Checking that Azure Container Apps is available in '$Region'..."
$locations = Invoke-Az provider show --namespace Microsoft.App `
    --query "resourceTypes[?resourceType=='managedEnvironments'].locations | [0]" --output json | ConvertFrom-Json
$available = @($locations | ForEach-Object { ($_ -replace '\s', '').ToLower() })
if ($available -notcontains $Region.ToLower()) {
    throw "Container Apps is not offered in '$Region'. Regions that offer it: $($locations -join ', ')"
}

Write-Host "Registering resource providers (first time only, takes a minute)..."
Invoke-Az provider register --namespace Microsoft.App --wait | Out-Null
Invoke-Az provider register --namespace Microsoft.OperationalInsights --wait | Out-Null
Invoke-Az extension add --name containerapp --upgrade --only-show-errors | Out-Null

Write-Host "Creating resource group '$ResourceGroup' in $Region..."
Invoke-Az group create --name $ResourceGroup --location $Region --output none | Out-Null

if (-not (Test-AzResource containerapp env show --name $EnvName --resource-group $ResourceGroup)) {
    Write-Host "Creating Container Apps environment '$EnvName' (2-3 minutes)..."
    Invoke-Az containerapp env create --name $EnvName --resource-group $ResourceGroup --location $Region --output none | Out-Null
}

$minReplicas = 0
if ($Demo) { $minReplicas = 1 }

function Deploy-App {
    param(
        [string]$Name, [string]$Image, [int]$Port, [string]$Ingress,
        [string]$Cpu, [string]$Memory, [string[]]$EnvVars = @()
    )
    if (Test-AzResource containerapp show --name $Name --resource-group $ResourceGroup) {
        Write-Host "Updating $Name..."
        $cmd = @("containerapp", "update", "--name", $Name, "--resource-group", $ResourceGroup,
                 "--image", $Image, "--min-replicas", $minReplicas, "--max-replicas", 1, "--output", "none")
        if ($EnvVars.Count -gt 0) { $cmd += "--set-env-vars"; $cmd += $EnvVars }
    }
    else {
        Write-Host "Creating $Name..."
        $cmd = @("containerapp", "create", "--name", $Name, "--resource-group", $ResourceGroup,
                 "--environment", $EnvName, "--image", $Image, "--ingress", $Ingress, "--target-port", $Port,
                 "--cpu", $Cpu, "--memory", $Memory,
                 "--min-replicas", $minReplicas, "--max-replicas", 1, "--output", "none")
        if ($EnvVars.Count -gt 0) { $cmd += "--env-vars"; $cmd += $EnvVars }
        if ($GhcrUser) {
            $cmd += @("--registry-server", "ghcr.io", "--registry-username", $GhcrUser, "--registry-password", $GhcrToken)
        }
    }
    Invoke-Az @cmd | Out-Null
}

$registry = "ghcr.io/$($Owner.ToLower())"

# Backends first, so their names resolve when the frontend's nginx starts.
Deploy-App -Name "flare-api"      -Image "$registry/flare-api:$Tag"      -Port 8000 -Ingress "internal" -Cpu "0.25" -Memory "0.5Gi"
Deploy-App -Name "flare-routing"  -Image "$registry/flare-routing:$Tag"  -Port 8001 -Ingress "internal" -Cpu "0.5"  -Memory "1.0Gi"

# Apps in one environment resolve each other's bare name (http://<app-name>) through
# Azure's normal request-routing path, but a plain nginx `resolver` directive does its
# own DNS lookup as a separate step, and the bare name doesn't resolve that way at all.
# Rather than guess the internal-FQDN pattern by hand (it isn't consistent across
# docs/environments - this one has no ".internal." segment at all), ask Azure for each
# app's own real address, the same way this script already does for flare-frontend below.
$apiFqdn = Invoke-Az containerapp show --name "flare-api" --resource-group $ResourceGroup `
    --query properties.configuration.ingress.fqdn --output tsv
$routingFqdn = Invoke-Az containerapp show --name "flare-routing" --resource-group $ResourceGroup `
    --query properties.configuration.ingress.fqdn --output tsv

Deploy-App -Name "flare-frontend" -Image "$registry/flare-frontend:$Tag" -Port 8080 -Ingress "external" -Cpu "0.25" -Memory "0.5Gi" `
    -EnvVars @(
        "API_UPSTREAM=https://$apiFqdn",
        "ROUTING_UPSTREAM=https://$routingFqdn",
        "DNS_RESOLVER=168.63.129.16"
    )

$fqdn = Invoke-Az containerapp show --name "flare-frontend" --resource-group $ResourceGroup `
    --query properties.configuration.ingress.fqdn --output tsv

Write-Host ""
Write-Host "Deployed. Open:  https://$fqdn"
Write-Host "Health check:    https://$fqdn/healthz"
if ($Demo) { Write-Host "Demo mode: one replica of each app stays running (uses credit). Re-run without -Demo afterwards to scale back to zero." }
else       { Write-Host "Idle apps scale to zero, so the first request after a quiet period takes a few seconds." }
Write-Host "Delete everything when finished:  ./infra/azure/deploy.ps1 -Teardown"
