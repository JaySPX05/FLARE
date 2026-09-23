# Git Workflow — FLARE

**Part 1** is the team policy. **Part 2** is a beginner walkthrough if you have not used git in a team before. **Part 3** is a block you can paste into your AI assistant so it can guide you through this project specifically.

---

# Part 1 — Team Policy

Deliberately lightweight. Six people working in parallel under time pressure need *enough* process to avoid stepping on each other, and no more.

## Branch model

```
main            ← always demo-able. Never commit directly.
  └── dev       ← integration branch. Everyone merges here.
        ├── feat/data-terrain-cleanup       (Role A)
        ├── feat/swmm-network-conversion    (Role B)
        ├── feat/susceptibility-overlay     (Role C)
        ├── feat/api-risk-endpoint          (Role D)
        ├── feat/map-time-slider            (Role E)
        └── chore/docker-compose            (Role F)
```

**`main` must always run.** On demo day, you demo from `main`. If `main` is broken, you have no demo.

## Branch naming

`<type>/<role-area>-<short-description>`

| Type | Use for |
|---|---|
| `feat/` | new functionality |
| `fix/` | bug fixes |
| `chore/` | tooling, deps, config |
| `docs/` | documentation only |

Examples: `feat/api-routing-dijkstra`, `fix/map-legend-overflow`, `chore/ci-lint`

## Daily rhythm

1. `git pull origin dev` **before you start working** — every session, no exceptions
2. Work on your own `feat/` branch
3. Commit small and often (see message format below)
4. Push and open a PR into `dev` when your piece works
5. One teammate skims it and merges — don't gate on deep review during a sprint
6. `dev` → `main` only when the full pipeline runs end-to-end

## Commit messages

```
<area>: <what changed, imperative mood>

data: add CRS reprojection for drain shapefile
api: return mock risk payload matching contract v1.0.0
map: wire time slider to /api/v1/risk
swmm: handle missing pipe diameters with ward-median fallback
```

Areas: `data`, `swmm`, `ml`, `api`, `map`, `infra`, `docs`

Skip the ceremony, but make the first line say what actually changed — "fixes" and "updates" tell future-you nothing at 2am.

## What never gets committed

Enforced by `.gitignore`, but worth understanding *why*:

- **Raw and processed geodata** (`.tif`, `.shp`, `.geojson`) — GitHub hard-blocks files over 100MB and DEM tiles blow past that. Everyone regenerates via `data-pipeline/download_data.sh`.
- **Trained ML models** (`.pkl`, `.joblib`) — build outputs. Regenerate from the committed Colab notebook.
- **SWMM outputs** (`.out`, `.rpt`) — regenerate from the committed `.inp`.
- **Secrets** (`.env`, API keys) — if one is ever committed, rotate the key immediately; removing the file doesn't remove it from history.

If you genuinely need to share a large processed file, put it in shared cloud storage and link it in the relevant README — don't commit it.

## The one hard rule: the API contract

`contracts/risk-api-schema.json` is what lets Backend, Frontend, and ML build **at the same time** instead of waiting on each other.

- Freeze it in the first hour
- Changing its shape requires a message in the team channel **first**
- Bump `schema_version` when it changes

A silent shape change breaks someone else's work without warning and costs more time than the change saved.

## Merge conflicts

Because each role owns a distinct top-level folder, real conflicts should be rare. When they happen it's usually in a shared file (`contracts/`, root `README.md`, `docker-compose.yml`) — talk to the other person rather than blindly picking a side.

## Demo-day discipline

- **Freeze the code** at the point specified in the 2-day plan (Day 2, Hr 8–9)
- Tag it: `git tag -a v1.0-demo -m "SIH demo build"` and push the tag
- After the freeze, only fixes for demo-breaking bugs — no new features
- Have the demo running from a **clean clone** at least once before you present, so you catch "works on my machine" problems while there's still time

---

# Part 2 — For Teammates New to Git

Everything above is the *policy*. This part is the *how*. If you've never used git in a team before, start here and follow it literally.

## The mental model

Git is a shared history of the project. Three ideas cover almost everything you'll do:

- **Branch** — your own copy of the project to work on, so your half-finished code never breaks anyone else's
- **Commit** — a save point in your branch, with a note about what you changed
- **Push / Pull** — send your commits up to GitHub / bring everyone else's down

`main` is the finished version. `dev` is where everyone's work gets combined. Your `feat/...` branch is your personal workspace.

## One-time setup

```bash
# Tell git who you are (once per machine)
git config --global user.name "Your Name"
git config --global user.email "your@email.com"

# Get the project onto your computer
git clone <repo-url>
cd jaldrishti
```

## The loop you'll repeat all day

```bash
# 1. Start from the latest shared code
git checkout dev
git pull origin dev

# 2. Make your own branch (name it for what you're doing)
git checkout -b feat/map-time-slider

# 3. ... write code ...

# 4. See what you changed
git status

# 5. Stage and save your work
git add .
git commit -m "map: add time slider component"

# 6. Send it to GitHub
git push origin feat/map-time-slider
```

Then open a Pull Request on GitHub from your branch into `dev`. A teammate skims it and merges.

Repeat from step 1 for your next piece of work.

## Commands you'll actually use

| Command | What it does |
|---|---|
| `git status` | What have I changed? **Run this constantly.** |
| `git pull origin dev` | Get everyone else's latest work |
| `git checkout -b feat/thing` | Make and switch to a new branch |
| `git checkout dev` | Switch back to the dev branch |
| `git branch` | Which branch am I on? |
| `git add .` | Stage all your changes for saving |
| `git commit -m "msg"` | Save staged changes with a message |
| `git push origin <branch>` | Upload your branch to GitHub |
| `git log --oneline` | See recent history |
| `git diff` | See exactly what you changed, line by line |

## When something goes wrong

Nothing here is unrecoverable. Git almost never actually loses committed work.

**"I'm on the wrong branch and haven't committed yet"**
```bash
git stash            # put changes aside
git checkout dev     # go where you meant to be
git stash pop        # bring changes back
```

**"I want to undo my last commit but keep the code"**
```bash
git reset --soft HEAD~1
```

**"I have a merge conflict"**
Git marks the clashing sections in the file like this:
```
<<<<<<< HEAD
your version
=======
their version
>>>>>>> dev
```
Open the file, delete the `<<<<<<<`, `=======`, `>>>>>>>` marker lines, keep the code you actually want (often a bit of both), then:
```bash
git add .
git commit
```
If you're unsure which version to keep, **ask the person who wrote the other half** — don't guess.

**"I accidentally committed a huge data file"**
Tell the team before pushing. Don't try to rewrite history alone.

**"I have no idea what state I'm in"**
```bash
git status
git branch
```
Then paste the output into the team channel or your AI assistant.

## Things not to do

- Don't commit directly to `main` — it must stay demo-able
- Don't `git push --force` unless someone experienced is guiding you
- Don't commit data files, trained models, or `.env` secrets (the `.gitignore` blocks most of this, but check `git status` before committing)
- Don't sit stuck for more than 15 minutes — ask

---

# Part 3 — Copy This Into Your AI Assistant

Paste the block below into Claude, ChatGPT, or whatever you use. It gives your assistant the context to guide you through this specific project's git setup, instead of giving generic advice that conflicts with how the team works.

**Copy the whole block below:**

```text
I'm working on a team software project called FLARE (Flood Localization & Alert Real-time Engine — an urban flood prediction system for a hackathon). I'm new to git and version control. Please act as a patient guide and help me with git commands, explaining what each one does before I run it.

Here is my team's exact git setup — please follow these conventions and don't suggest alternatives that conflict with them:

BRANCH STRUCTURE:
- `main` — must always be working/demo-able. Nobody commits to it directly.
- `dev` — the integration branch. Everyone merges their work here via Pull Requests.
- `feat/<area>-<description>` — personal working branches, e.g. `feat/api-risk-endpoint`, `feat/map-time-slider`
- Branch prefixes: `feat/` for new work, `fix/` for bugs, `chore/` for tooling/config, `docs/` for documentation

MY DAILY WORKFLOW:
1. `git pull origin dev` before starting work, every session
2. Work on my own `feat/` branch
3. Commit small and often
4. Push and open a PR into `dev`
5. A teammate skims and merges

COMMIT MESSAGE FORMAT:
`<area>: <what changed, imperative mood>`
Areas are: data, swmm, ml, api, map, infra, docs
Examples:
- `data: add CRS reprojection for drain shapefile`
- `api: return mock risk payload matching contract v1.0.0`
- `map: wire time slider to /api/v1/risk`

WHAT MUST NEVER BE COMMITTED (the .gitignore blocks these, but I should double-check with `git status`):
- Geospatial data files (.tif, .shp, .geojson) — too large for GitHub, we regenerate them from scripts
- Trained ML models (.pkl, .joblib) — build outputs, regenerated from a Colab notebook
- SWMM outputs (.out, .rpt) — regenerated from committed .inp files
- Secrets (.env, API keys)

REPO STRUCTURE (each folder is owned by a different teammate):
- `contracts/` — shared API schema, the file that lets everyone work in parallel
- `data-pipeline/` — data engineering & GIS
- `hydrology-swmm/` — hydraulic modeling
- `ml-nowcasting/` — nowcasting & machine learning
- `backend-api/` — FastAPI backend
- `frontend-dashboard/` — the map dashboard
- `infra/` — deployment & integration
- `data/` — local data, gitignored

ONE HARD RULE: the file `contracts/risk-api-schema.json` defines the API shape that the backend, frontend, and ML pieces all build against. Changing its shape requires telling the team first and bumping its schema_version.

HOW I WANT YOU TO HELP ME:
- When I describe what I'm trying to do, give me the exact commands for this setup
- Explain what each command does before I run it, in plain language
- If I paste an error or the output of `git status`, tell me what state I'm in and what to do next
- Warn me if I'm about to do something risky (force push, committing large files, committing to main)
- Assume I don't know git jargon — explain terms like "staging", "HEAD", "upstream" when you use them

My first question is:
```

Then add your own question at the end — for example:
- *"How do I start working on my first task?"*
- *"I ran `git status` and got this output: [paste it]. What do I do?"*
- *"I have a merge conflict in `backend-api/main.py`. Walk me through it."*
