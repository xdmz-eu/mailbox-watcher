# mailbox-watcher

A serverless IMAP watcher that notifies other mailboxes about incoming email.

It is built for high-privilege "owner" mailboxes (account recovery, domain and
billing contacts) that must not forward mail. Instead of forwarding, the
watcher periodically checks each watched mailbox read-only and sends a short
metadata report to configured recipients. Message bodies are never forwarded,
so a recovery code cannot leak to a recipient mailbox through the watcher.

## How it works

```text
          every 6 h (CRON, UTC)
                  │
                  ▼
┌──────────────────────────────────────┐        IMAPS, read-only (EXAMINE,
│ Scaleway Serverless Function         │        BODY.PEEK header fields)
│ handler.handle                       │ ─────────────────────────────────▶ watched mailboxes
│                                      │
│  1. compute last complete window     │
│  2. per mailbox: collect metadata    │        HTTPS (TEM API)
│  3. per mailbox: send report/alert   │ ─────────────────────────────────▶ notify recipients
│  4. optional daily heartbeat         │
└──────────────────────────────────────┘
```

* **Windows without state.** Each run reports the last complete interval
  aligned to 00:00 UTC (for 6 h: 00–06, 06–12, 12–18, 18–24). A run at 12:05
  reports 06:00–12:00, filtered precisely by the server's INTERNALDATE.
  Consecutive runs tile without gaps or duplicates and no state is stored.
* **Reports** are sent only if a mailbox received messages. One report per
  watched mailbox lists Received, From, To/Cc and Date of every message.
  The Subject is omitted unless `include_subject` is enabled for that
  mailbox; it can contain codes and is therefore off by default.
* **Failure alerts** are sent to a mailbox's recipients when it cannot be
  checked (authentication, TLS, connection, missing password). They contain a
  short category only, never server responses or secrets. Messages from a
  failed window are not reported later; check the mailbox directly.
* **Heartbeat** (optional): once a day, at the configured window boundary, a
  status summary confirms that the watcher runs at all.

## Repository layout

```text
src/                         Function archive root (zipped by Terraform)
  handler.py                 Scaleway entry point: handler.handle
  mailbox_watcher/
    config.py                JSON configuration parsing and strict validation
    window.py                Stateless, UTC-aligned report windows
    imap.py                  Read-only IMAP metadata collection
    report.py                Plain-text reports, alerts and heartbeat
    tem.py                   Scaleway Transactional Email sender (+ dry-run printer)
    runner.py                One watcher run: orchestration and result summary
    __main__.py              Local CLI: --validate, --dry-run
tests/                       Unit tests (standard library only, no network)
terraform/                   Scaleway infrastructure and deployment
  tests/                     Offline `terraform test` with a mocked provider
scripts/set-function-secret.sh  Sets one secret on the deployed function
config.example.json          Configuration example (fake addresses)
.github/workflows/           CI (tests, Terraform checks) and deployment
```

The function uses the Python standard library only; there are no runtime
dependencies to update.

## Configuration

The watcher reads one JSON document from `MAILBOX_WATCHER_CONFIG`. It
contains no passwords; each mailbox names the environment variable that holds
its IMAP password. See [`config.example.json`](config.example.json).

| Key | Description |
| --- | --- |
| `sender.email`, `sender.name` | Sender address on a TEM-verified domain. |
| `interval_hours` | Window length; must divide 24 and match Terraform's `interval_hours`. Default 6. |
| `mailboxes[].name` | Label used in reports (lowercase, digits, hyphens). |
| `mailboxes[].host`, `port` | IMAPS server, default port 993. TLS certificates are verified. |
| `mailboxes[].username` | IMAP login. |
| `mailboxes[].password_env` | Name of the secret environment variable with the password. |
| `mailboxes[].folders` | Folders to watch, default `["INBOX"]`. ASCII names only. |
| `mailboxes[].notify` | Recipients of reports and alerts for this mailbox. |
| `mailboxes[].include_subject` | Include the Subject header. Default `false`. |
| `heartbeat.hour_utc`, `heartbeat.notify` | Optional daily status at a window boundary. |

Unknown keys, duplicate keys and inline passwords are rejected. Keep your real
configuration out of Git (`config.json` is ignored).

### Secrets

Secrets are set directly on the function as Scaleway secret environment
variables. They never pass through Git, GitHub or the Terraform state;
Terraform ignores changes to them.

| Variable | Content |
| --- | --- |
| one per mailbox, as named in `password_env` | IMAP app password (for Infomaniak: a dedicated device password per mailbox). |
| `TEM_SECRET_KEY` | Secret key of an IAM application that may only send TEM email. |

```sh
sh scripts/set-function-secret.sh "$(terraform -chdir=terraform output -raw function_id)" TEM_SECRET_KEY
sh scripts/set-function-secret.sh "$(terraform -chdir=terraform output -raw function_id)" MAILBOX_OWNER_EXAMPLE_ORG_PASSWORD
```

The Scaleway console (function → Settings → Secrets) works as well.

## Local use

```sh
cd src
python -m mailbox_watcher --config ../config.json --validate
# Check the real mailboxes and print the notifications instead of sending:
MAILBOX_OWNER_EXAMPLE_ORG_PASSWORD=... python -m mailbox_watcher --config ../config.json --dry-run
```

Tests:

```sh
python -m unittest discover -s tests -t tests -v
terraform -chdir=terraform init -backend=false && terraform -chdir=terraform test
```

## One-time Scaleway setup

Done manually with an organization owner account, so the deployment key never
needs IAM permissions.

1. Create the project for the watcher.
2. Create the Terraform state bucket in Object Storage and enable versioning.
3. Create an IAM application `mailbox-watcher-deploy` with a policy scoped to
   this project: Serverless Functions full access, Object Storage access to
   the state bucket and Transactional Email domain management. No IAM and no
   Secret Manager permissions. Create an API key for it.
4. Create an IAM application `mailbox-watcher-mailer` with permission to send
   Transactional Email in this project only. Its secret key becomes
   `TEM_SECRET_KEY`.
5. After the first deployment, publish the DNS records from
   `terraform output tem_dns_records` for the sender domain and wait for TEM
   to verify the domain.
6. Set the function secrets (see above) and trigger a test run.

## Deployment

Pushing to `main` runs the tests and then `terraform apply` in the GitHub
environment `production`. Terraform packages `src/` and updates the function
code, its configuration, the CRON trigger and the TEM domain in one step.

Configure the `production` environment with required reviewers and a
deployment branch policy limited to `main`, and add:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `SCW_ACCESS_KEY`, `SCW_SECRET_KEY` | Key of `mailbox-watcher-deploy`. |
| Secret | `WATCHER_CONFIG` | Your private configuration JSON. |
| Variable | `SCW_PROJECT_ID` | Project ID. |
| Variable | `SCW_REGION` | Optional, default `fr-par`. |
| Variable | `TF_STATE_BUCKET` | State bucket name. |
| Variable | `TEM_DOMAIN` | Sender domain, or empty to manage TEM elsewhere. |

### Security notes

* The function is private; only its CRON trigger invokes it.
* Anyone who can push to `main` (or approve the `production` environment)
  can deploy code that reads the function's secrets. Protect the repository
  accordingly: branch protection, signed commits, hardware-backed 2FA.
* Use a dedicated, revocable IMAP password per watched mailbox.

## Status and open points

* Verify on the first deployment that code updates through Terraform keep the
  secret environment variables set out of band.
* Verify that the `scw function function update` call in
  `scripts/set-function-secret.sh` adds or updates a single secret without
  removing the others.
* Pin GitHub Actions to commit SHAs.
* Commit `terraform/.terraform.lock.hcl` after the first `terraform init`.
