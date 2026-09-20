# Deploying to Azure (Container Apps)

Target: three Azure Container Apps in one environment, images pulled from GitHub Container Registry
(free; avoids Azure Container Registry's daily fee and its region restrictions on student subscriptions).

```
Internet -> jaldrishti-frontend (public, nginx) -> jaldrishti-api      (internal)
                                                -> jaldrishti-routing  (internal)
```

## One-time setup
1. Install the Azure CLI, then `az login` (use the account that owns the Azure for Students subscription).
2. Merge to `main` so the **Container images** workflow builds and pushes the three images.
3. On GitHub -> your profile -> **Packages**: open `jaldrishti-api`, `jaldrishti-routing`, `jaldrishti-frontend`
   -> Package settings -> **Change visibility -> Public** (so Azure can pull them without credentials).
   If you prefer private packages, pass `-GhcrUser <you> -GhcrToken <PAT with read:packages>` to the script.

## Deploy
```powershell
./infra/azure/deploy.ps1                 # cheapest: idle apps scale to zero
./infra/azure/deploy.ps1 -Demo           # demo day: keep one replica of each warm
./infra/azure/deploy.ps1 -Region koreacentral   # if centralindia is refused for your subscription
```
Re-running the script updates the apps to the newest `latest` images.

## Stop the meter
```powershell
./infra/azure/deploy.ps1 -Teardown       # deletes the whole resource group
```

## Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| `RequestDisallowedByAzure` | The region isn't in your subscription's allowed list (Azure Policy -> Assignments -> "Allowed resource deployment regions"). Use `-Region` with one that is. |
| "Container Apps is not offered in ..." | Pick a region from the list the script prints. |
| Site shows 502 for a few seconds after idle | Cold start from scale-to-zero. Use `-Demo` before presenting. |
| App won't start | `az containerapp logs show -n jaldrishti-routing -g jaldrishti-rg --follow` |
| Image pull error | The ghcr.io package is still private, or the workflow hasn't pushed the image yet. |

## Cost notes
Container Apps include a monthly free grant per subscription (180,000 vCPU-s, 360,000 GiB-s, 2M requests).
Scale-to-zero apps that stay inside it cost nothing; `-Demo` replicas bill at the idle rate and come out of your credit.
The environment also creates a small Log Analytics workspace for logs.
