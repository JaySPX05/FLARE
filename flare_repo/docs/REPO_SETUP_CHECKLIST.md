# Repo Setup Checklist — Git Owner

**Who:** One person from Pair 2 (Backend & Routing)
**When:** Day 1, Hour 0–1, during the kickoff sync
**How long:** ~15 minutes

Pair 2 owns this because they sit in the middle of the dependency chain — they consume Pair 1's output and produce what Pair 3 depends on, so they feel integration breakage first. Pair 1 is the critical path on Day 1 and Pair 3 needs Day 2 polish time; don't interrupt either.

---

## Before kickoff — tell the team

Send this the night before:

> Everyone create a GitHub account before we start tomorrow, and set up a Personal Access Token (GitHub → Settings → Developer settings → Personal access tokens → generate one with `repo` scope). Save the token somewhere — you'll use it instead of a password when git asks. We'll lose an hour if we do this during the sprint.

**This is the single most common time-waster for teams new to git.** GitHub stopped accepting passwords over HTTPS; without a token or SSH key, nobody can push.

---

## Step 1 — Create the repository

- [ ] github.com → **New repository**
- [ ] Name: `jaldrishti`
- [ ] Visibility: **Private** (flip to public before submission if SIH requires it)
- [ ] **Do NOT** check "Add a README", ".gitignore", or "license" — the scaffold already has these and pre-adding them causes a conflict on first push
- [ ] Click **Create repository**

Ignore the setup commands GitHub shows you; use Step 2 instead.

---

## Step 2 — Push the scaffold

Extract `jaldrishti-repo.tar.gz`, then:

```bash
cd jaldrishti-repo

git init
git add .
git commit -m "chore: scaffold repo structure, API contract, and git workflow"

git branch -M main
git remote add origin https://github.com/<your-username>/jaldrishti.git
git push -u origin main
```

- [ ] Scaffold pushed to `main`

Create the integration branch:

```bash
git checkout -b dev
git push -u origin dev
```

- [ ] `dev` branch created and pushed

---

## Step 3 — Add teammates

- [ ] Repo → **Settings** → **Collaborators** → **Add people**
- [ ] Add all five teammates by GitHub username
- [ ] Confirm each has accepted the invite (they get an email)

---

## Step 4 — Protect `main`

This is what stops someone accidentally breaking the demo build.

- [ ] Repo → **Settings** → **Branches** → **Add branch protection rule**
- [ ] Branch name pattern: `main`
- [ ] Check **Require a pull request before merging**
- [ ] Set required approvals to **0 or 1** — requiring 2 reviewers creates a bottleneck in a sprint

Leave `dev` unprotected so people can merge quickly.

---

## Step 5 — Commit the API contract

This is the first *real* commit and it unblocks all three pairs.

- [ ] Team has agreed the API shape in the kickoff
- [ ] `contracts/risk-api-schema.json` reflects that agreement
- [ ] Committed and pushed to `dev`

---

## Step 6 — Get everyone cloned

Send the team:

```bash
git clone https://github.com/<your-username>/jaldrishti.git
cd jaldrishti
git checkout dev
```

- [ ] All five teammates have cloned successfully and can run `git status` without errors
- [ ] Everyone has read the README in their own folder
- [ ] Everyone has pasted the AI primer block (Part 3 of the Git Workflow Guide) into their own assistant

---

## Step 7 — Brief the team (10 minutes)

Walk through the workflow doc and state these two rules out loud:

1. **The 15-minute rule.** Stuck on git for 15 minutes → ask the git owner. No exceptions, no silent struggling.
2. **The contract rule.** If the API shape must change, say so in the team channel *before* changing it. A silent shape change breaks someone else's work without warning.

- [ ] Team briefed

---

## Your ongoing duties (both days)

- Merge PRs into `dev` — skim, don't deep-review. It's a sprint.
- Unblock anyone stuck on git rather than letting them lose 30 minutes.

Total ongoing effort: maybe 30–45 minutes across both days.

---

## Day 2, Hour 8–9 — Code freeze

- [ ] Merge `dev` → `main`
- [ ] Tag it:
  ```bash
  git tag -a v1.0-demo -m "SIH demo build"
  git push origin v1.0-demo
  ```
- [ ] **Clone the repo fresh into a new folder and confirm the demo runs from it** — this catches "works on my machine" problems while there's still time to fix them
- [ ] Announce the freeze: only demo-breaking bug fixes from here, no new features

---

## Troubleshooting

**"Authentication failed" on push**
The person is using their password. They need their Personal Access Token instead. See the pre-kickoff note above.

**"Push rejected — file too large"**
Someone committed a data file. GitHub blocks files over 100MB; our CI blocks over 50MB. Don't try to rewrite history alone — tell the team, and have them remove the file from the commit before pushing.

**"Updates were rejected because the remote contains work you do not have"**
They need to pull first: `git pull origin dev`, resolve anything that conflicts, then push again.

**Someone committed to `main` directly**
If branch protection is on, this can't happen. If it did happen, protection wasn't enabled — go back to Step 4.

---

## If GitHub is being difficult

GitLab and Bitbucket work identically and the whole workflow doc applies unchanged. GitHub is just the lowest-friction option since most people already have accounts.
