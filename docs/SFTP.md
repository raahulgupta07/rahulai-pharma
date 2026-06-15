# SFTP Drop-Folder Ingest

Let pharmacies push data files over **SFTP**. They drop a CSV/Excel into their own
jailed folder; the platform auto-ingests it into a table and retrains — no app
login, no access to anything else.

Everything is managed from **`/command-center` → Data → SFTP Access** (superadmin only).

---

## How it works

```
 Pharmacy            cp-sftpgo (sidecar)            cp-api (app)
  sftp put  ───────▶  SFTPGo, jailed per user  ───▶  sftp_watch daemon (30s)
   key/pass            users in OUR Postgres          │ reads /app/sftp_data (read-only)
                       (sftpgo_* tables)              ▼
                              │ writes          POST /api/upload?guarded=1
                       shared volume sftp_data   ─▶ table citypharma.<feeds_table>
                                                  ─▶ auto-train ─▶ answerable in chat
```

- **SFTP users are NOT app users.** They live in SFTPGo's own Postgres tables
  (prefix `sftpgo_`), never in `dash_users`. They have zero access to the app, DB,
  chat, or other users — only their own folder.
- **The app mount is read-only.** The watcher reads drops; it never writes into a
  user's jail. File cleanup is done by SFTPGo itself (retention API).
- **Ingest reuses `/api/upload?guarded=1`** — same parser, same empty/drift/cliff
  guards + pre-replace backup as S3 sync, same auto-train.

---

## Managing users (admin console)

`/command-center → Data → SFTP Access`:

- **+ Add SFTP user** — username; **SSH key** (paste theirs or *Generate keypair*)
  or **Password** (auto-generated); permission **Upload-only** (sees own folder +
  uploads, cannot download/delete) or **Read+write**; **Feeds Table** (which table
  their files load into); allowed file types; quota; max sessions; expiry.
- **Row actions** — reset password / rotate key / edit / disable / delete /
  **browse drops**.
- Secrets (key or password) are shown **once** — copy and hand to the pharmacy
  over a secure channel.
- **Recent ingests** feed shows what was loaded (file → table → status → rows).
- **Purge drops older than N days** — clears accumulated files (data in tables
  stays).

A user with **no Feeds Table** set → their drops are logged as an error and admins
are notified; nothing is ingested (fail-closed, never guesses a table).

---

## Connecting (what the pharmacy does)

```
sftp -P 2222 acme@YOUR-SERVER        # password, or:
sftp -i acme_key -P 2222 acme@YOUR-SERVER
# then:  put balance_stock.csv
```
FileZilla / WinSCP work too (SFTP, port 2222, their username + key/password).

---

## Configuration (.env)

| Var | Default | Meaning |
|-----|---------|---------|
| `SFTPGO_ADMIN_USER` | `sftpadmin` | SFTPGo admin (app uses it via REST). |
| `SFTPGO_ADMIN_PASS` | — | **Required.** Strong random. Never commit. |
| `SFTPGO_BASE_URL` | `http://cp-sftpgo:8080` | Internal admin REST URL. |
| `SFTP_PUBLIC_HOST` | — | Hostname shown in the console connect hint. |
| `SFTP_PUBLIC_PORT` | `2222` | Public SFTP port. |
| `SFTP_WATCH_ENABLED` | `1` | Watcher on/off. |
| `SFTP_WATCH_TICK_SECONDS` | `30` | Poll interval. |
| `SFTP_STABLE_SECONDS` | `20` | Skip files younger than this (half-uploaded). |
| `SFTP_MAX_FILE_MB` | `1024` | Per-file size cap. |
| `SFTP_RETENTION_HOURS` | `0` | Auto-delete drops older than N hours (0 = keep; UI can purge manually). |

---

## Security model

Defence in depth — each layer independent:

1. **Network** — SFTP port `2222` must be firewalled. SFTPGo admin port is bound
   to `127.0.0.1` only (never public). Brute-force auto-ban (defender) is on.
2. **Auth** — prefer SSH keys over passwords. Per-user expiry + max sessions.
3. **Isolation** — virtual users, per-user chroot, no shell. Upload-only =
   no download (no data exfiltration), no delete.
4. **Ingest** — `guarded=1`: a corrupt/empty/truncated/short file can't wipe or
   silently corrupt a live table (backup + drift + cliff guards). Per-user
   Feeds Table allow-list — one pharmacy can never touch another's data.
5. **Container** — non-root caps dropped (`cap_drop: ALL`), `no-new-privileges`,
   read-only `/tmp`, image **digest-pinned**.
6. **Audit** — every user create/delete/retention written to `dash_audit_log`;
   every ingest to `dash_sftp_ingest_log`; failures notify admins (bell).

### AWS deployment checklist (engineer)

- [ ] **Security Group**: allow TCP **2222** only from the pharmacies' known IPs
      (or put SFTP behind a VPN). Do NOT open `0.0.0.0/0`.
- [ ] Do **not** publish port `8092` (SFTPGo admin) — it stays localhost-bound.
- [ ] Set a strong `SFTPGO_ADMIN_PASS` in the deploy secret store.
- [ ] Set `SFTP_PUBLIC_HOST` to the public hostname (for the connect hint).
- [ ] Prefer SSH-key auth; issue keypairs from the console.
- [ ] Consider `SFTP_RETENTION_HOURS=168` (7 days) so drops don't accumulate.
- [ ] EBS volume encryption on for `sftp_data` (at-rest).
- [ ] Re-pin the SFTPGo image digest deliberately on upgrade.

---

## Components

| Piece | File |
|-------|------|
| SFTPGo sidecar + shared volume | `compose.yaml` (`dash-sftpgo`, `sftp_data`) |
| Admin REST wrapper + endpoints | `app/sftp_admin.py` (`/api/admin/sftp/*`) |
| Ingest watcher daemon | `dash/cron/sftp_watch_daemon.py` |
| Console panel | `frontend/src/lib/admin/SftpAccessPanel.svelte` |
| Ingest log table | `public.dash_sftp_ingest_log` (auto-created) |

## Troubleshooting

- **"server unreachable"** in console → `docker logs cp-sftpgo`; check
  `SFTPGO_ADMIN_PASS` matches between `cp-api` and `cp-sftpgo`.
- **File dropped but not ingested** → check the Recent Ingests feed / `dash_sftp_ingest_log`.
  Common causes: no Feeds Table mapped, unsupported type, still within the 20s
  stability window, or a `held` (guard) status (fix the file, re-drop).
- **`sftp` client says "Need cwd"** → the user must have at least the `list`
  permission ("Upload-only" already includes it).
