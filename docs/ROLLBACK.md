# Releases & Rollback

Every release is a git tag **and** a registry image, so you can always go back.

## How a release happens

1. Make changes, test locally (deploy to `:8011`).
2. Bump `VERSION`, add notes to `CHANGELOG.md` (internal) and `docs/CHANGELOG.json` (customer-facing), commit.
3. `scripts/release.sh` → tags `vX.Y.Z` and pushes.
4. `.github/workflows/release.yml` builds the image and pushes:
   - `ghcr.io/raahulgupta07/citypharma:X.Y.Z`
   - `ghcr.io/raahulgupta07/citypharma:latest`

So GitHub holds the **source** at that tag and GHCR holds the **exact image** for that version. Nothing is lost on the next build.

## How a deploy happens (registry-pull model)

`.env`:
```
IMAGE_NAME=ghcr.io/raahulgupta07/citypharma
IMAGE_TAG=1.49.0
```
Deploy:
```
docker compose pull dash-api
docker compose up -d --no-build --force-recreate dash-api
```
The running container is now byte-identical to what CI built for that git tag.

> Local-build fallback (offline / no registry): omit `IMAGE_NAME`, build locally, and
> after every build run `docker tag citypharma:latest citypharma:$(cat VERSION)` so old
> versions survive on the box. Registry-pull is preferred — it can't drift from GitHub.

## How to roll back (something shipped broken)

One command — pulls the old image, recreates only the app, keeps the DB:
```
scripts/rollback.sh 1.48.0
```
Add `--code` to also move the working tree to that source tag:
```
scripts/rollback.sh 1.48.0 --code
```
To make it stick, set `IMAGE_TAG=1.48.0` in `.env`.

Rollback touches **only** `dash-api`. `cp-db`, `cp-redis`, `cp-pgbouncer` stay up — no data is moved.

## ⚠️ The one thing rollback does NOT undo: database migrations

Migrations (`db/migrations/`) are **forward-only**. Rolling the image back to 1.48.0
does **not** drop columns/tables that a 1.49.0 migration added.

- **Additive migration** (new column/table, nullable, no drops) → safe to roll back. Old
  code simply ignores the new column. This is the normal case.
- **Breaking migration** (renamed/dropped column, type change, backfill the old code
  can't read) → rolling code back can crash against the new schema. Before rolling back:
  1. Check what migrated since the target: `ls db/migrations/` and diff against the tag.
  2. If a breaking change shipped, either (a) roll forward with a hotfix instead, or
     (b) restore the DB from the pre-migration backup (`cp-backup` / `*__bak` snapshot)
     **before** rolling code back.

**Rule for authors:** keep migrations additive and backward-compatible within a minor
series. Stage destructive changes (add new → backfill → switch reads → drop old) across
releases so any single rollback is always safe.

## Quick reference

| Action | Command |
|--------|---------|
| Cut a release | bump VERSION + changelogs, commit, `scripts/release.sh` |
| Dry-run a release | `scripts/release.sh --dry-run` |
| Deploy a version | `IMAGE_TAG=X.Y.Z` in `.env` → `docker compose pull dash-api && docker compose up -d dash-api` |
| Roll back | `scripts/rollback.sh <old-version>` |
| List released versions | `git tag --list 'v*'` |
| See what migrated since | `ls db/migrations/` + diff vs tag |
