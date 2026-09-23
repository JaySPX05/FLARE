# infra — Role F: Integration, Citizen Features & Deployment

**Goal:** Make this a deployable product, not six separate scripts. You are the integration glue.

## You own
- End-to-end integration testing: Role A → B/C → D → E actually connects
- Deployment: whole stack on one cloud VM or a documented local setup, reliably demo-able
- Citizen reporting PWA *(Phase 2)*
- BBMP drain-prioritization analytics *(Phase 2)* — turns risk history into a ranked "fix these drains first" view
- Keeping the pitch deck's technical claims aligned with what's **actually built**

## Do this in week one / hour one
Set up the shared repo structure and a basic CI check (does the code even run) **before** individual pieces get complex. Integration pain compounds.

## Suggested structure
```
infra/
  docker-compose.yml
  Dockerfile.backend
  Dockerfile.frontend
  .github/workflows/ci.yml   # lint + import check, nothing heavy
  deploy_notes.md
```

## Depends on
Everyone.

## Others depend on you for
The demo working end-to-end on demo day.
