# GauTrack

A registry of cattle and their keepers for the Municipal Council, Rewari, with a field
app for officers on the road, an admin dashboard, a single-screen view for the district
leadership, and a public form for reporting an animal on the road. Built as a working
pilot; demo data only until the department decides otherwise.

## What is in this repository

| Path | What it is |
|---|---|
| `mvp/` | The whole application: web server, database migrations, field app, dashboards, tests |
| `mvp/README.md` | The operator runbook: install, run, demo, back up, deploy, harden. **Start here.** |
| `mvp/ARCHITECTURE.md` | How and why the code works, and an honest list of known weaknesses |
| `mvp/SPEC.md` and `mvp/DEVIATIONS.md` | What was specified, and every place the build departed from it, with reasons |
| `mvp/QUESTIONS.md` | The questions only the department can answer |
| `mvp/ACCESS_SHEET.html` | One printable page: who needs what to use the live system |
| `.devcontainer/` | Recipe for running the demo in a GitHub Codespace (runbook section 15) |

## The stack, in one paragraph

Python 3.12 with **FastAPI** (the web framework) serves everything: the HTML pages, the
JSON API and the field app, from one process on port 8000. Data lives in **PostgreSQL 16**.
In production **Caddy** (a reverse proxy: the program that answers on ports 80 and 443,
handles the HTTPS certificate, and passes requests to the Python process) sits in front.
The field app is a **PWA** (progressive web app: a web page the phone can install and use
offline) written in plain JavaScript, no build step, no Node. `docker-compose.yml` starts
the three pieces together.

## Run it on a laptop

```bash
cd mvp && make dev
```

Then open `http://localhost:8000/admin` (dashboard), `/app` (field app), `/cm` (leadership
view) and `/report` (public form). Passwords for the demo accounts are written to
`mvp/.seed_credentials.txt` on first run. `make test` runs the 71 tests against a throwaway
database; `make help` lists every command. Docker is optional on a laptop (a local
PostgreSQL is used if Docker is absent) and required on a server.

## Hosting: what was agreed, and why

- **The application needs its own subdomain**, for example `gautrack.mcrewari.in`. It
  cannot live under a path such as `mcrewari.in/gautrack/`: the pages, the field app's
  offline cache and the login cookies all assume they own the whole address. A subdomain
  is one DNS record in the office's Cloudflare account and needs no code change. The
  existing Drupal site is untouched; it only carries a link.
- **The DNS record:** type `A`, name `gautrack`, value = the server's public IP. Set the
  Cloudflare proxy to **off** ("DNS only", grey cloud) so that Caddy can obtain its own
  Let's Encrypt certificate and see the real visitor addresses, which the rate limits
  depend on. If the office insists on the orange cloud, set Cloudflare SSL to
  *Full (strict)* and `TRUSTED_PROXY_COUNT=2` in `mvp/.env`.
- **The server:** a Hostinger **KVM VPS** (a virtual private server: a whole Linux machine
  that stays on), Ubuntu 24.04, 2 to 4 vCPU, 8 GB RAM, 100 GB disk, in the **India
  (Mumbai)** region so the data stays in the country. Ordinary shared "web hosting" cannot
  run this: there is no long-lived Python process or PostgreSQL there.
- **Photos are files on disk**, not rows in PostgreSQL, so the database has no image-size
  concern. Each photo is capped at 5 MB and the phone shrinks it before upload.
- **First run on the server** is runbook section 16, step by step. Note that the Docker
  stack has been written and reviewed but the VPS will be its first real execution; expect
  to fix small things on the first `docker compose up`. Section 17 is the hardening
  checklist and section 19 explains the later move to HARTRON / State Data Centre / NIC.

## Checking it is safe

- `make test` is the regression suite; the authorisation, audit-chain, photo and export
  tests are the security tests.
- `make audit` checks the pinned Python packages against the public vulnerability database
  (`pip-audit`) and lints the code for security mistakes (`bandit`).
- `make verify-chain` proves nothing in the audit log or event history has been altered.
- Once the site is up, run an OWASP ZAP baseline scan against it and check the headers at
  Mozilla Observatory. Before real citizen data goes in, a CERT-In empanelled auditor must
  sign off; runbook section 20 is the checklist they start from.

## Working on the code

Work on a branch and open a pull request; do not push to `main` directly. Never commit
`mvp/.env`, `mvp/.seed_credentials.txt` or anything under `mvp/data/`: they are ignored by
`.gitignore` and must stay that way, because a secret that reaches the repository is
compromised even after it is deleted. This repository is private and must remain private;
the runbook explains why in section 15.

To give a developer access (the repository owner runs this once, with the person's
GitHub username):

```bash
gh api -X PUT repos/<owner>/gautrack/collaborators/<github-username> -f permission=push
```

The same thing is available on GitHub under Settings, Collaborators, Add people.
