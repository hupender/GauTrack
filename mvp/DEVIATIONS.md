# Deviations from `SPEC.md`

Every place the build differs from the spec, with the reason. Nothing security-related
was dropped; where the literal instruction was impossible on this machine, the closest
secure alternative was implemented and is recorded here.

Status key: **[env]** forced by this laptop · **[+]** stronger than the spec asked ·
**[~]** equivalent alternative · **[!]** genuinely weaker, needs attention.

---

## 1. Environment: no Docker on the build machine — **[env]**

**Spec §1.1:** "Everything runs with `docker compose up` (services: `db`, `api`, `caddy`)."

**What happened:** `docker` and `docker compose` are not installed on this Mac
(`docker --version` → command not found). Docker Desktop could not be installed and
started inside this task.

**What was done instead:**
- The full production stack is written and committed exactly as specified:
  `docker-compose.yml` (db + api + caddy, internal network, no published Postgres port,
  non-root API user), `docker-compose.dev.yml`, `Caddyfile`, `api/Dockerfile`,
  `api/entrypoint.sh`, `db/init/01_app_role.sh`. **It has not been executed**, so treat
  the first `docker compose up -d --build` on the VM as an untested step — see
  *Known gaps* in `README.md`.
- `make dev` works anyway: `scripts/dev_db.sh` prefers `docker compose` when Docker is
  present and otherwise falls back to a **real PostgreSQL 16.15** cluster
  (`brew install postgresql@16`) run with `pg_ctl` from a repo-local data directory
  (`mvp/.pgdata`) on port **55432**.
- The fallback deliberately does *not* use `brew services`: no launchd service is
  registered, nothing outside the repo is touched, and `make db-reset` removes it
  entirely.

**SQLite was never used**, for tests or otherwise. Everything — including the whole test
suite — runs on PostgreSQL 16, because the security model depends on Postgres-only
features (row triggers, `SECURITY DEFINER`, per-role `GRANT`s, `pg_trgm`, partial unique
indexes, JSONB).

Two macOS-specific fixes are baked into `scripts/dev_db.sh`, both commented in place:
`LC_ALL` must be set or the postmaster refuses to boot, and the Unix socket directory is
`/tmp/gautrack-pg` because `pg_ctl -o` splits its argument on whitespace and this repo's
path contains spaces.

---

## 2. Python 3.12 was not present — **[env]**

The machine had Python 3.11 and 3.14. The spec pins 3.12, so `python@3.12` was installed
via Homebrew and `mvp/.venv` is built from it (`Python 3.12.14`). No deviation in the
end; noted only because it was an extra install.

---

## 3. Split database roles: `gautrack_owner` and `gautrack_app` — **[+]**

**Spec §1.7:** "`events` table is append-only (no UPDATE/DELETE grants for the app role…)".

To make that literally true there has to *be* an app role distinct from the schema owner.
So there are two:

| role | used by | rights |
|---|---|---|
| `gautrack_owner` | Alembic, `seed.py`, `verify_chain.py`, `anchor.py`, backups | owns the schema |
| `gautrack_app` | the web process | no `UPDATE`/`DELETE` on `events`; `SELECT` only on `audit_log`; `SELECT` only on `ulbs`/`fine_schedule` |

The audit trigger is `SECURITY DEFINER`, so it can still write `audit_log` rows that the
application itself is not permitted to write, forge or delete. `tests/test_sync.py`
asserts the grants with `has_table_privilege`.

This adds one config value (`APP_DB_PASSWORD`) beyond the spec's list.

---

## 4. `events.seq` — one column added to the spec's schema — **[~]**

**Spec §2** lists the `events` columns; `seq BIGSERIAL UNIQUE` is not among them.

A hash chain is only meaningful over a **total order**. `events.id` is a client-generated
UUIDv7 whose timestamp comes from a field phone (wrong clocks, offline for hours), and
`received_at` can collide. Neither gives a dependable order. `seq` is assigned by
Postgres at insert time and is what both the chain trigger and `verify_chain.py` order by.

---

## 5. Session table stores a hash of the cookie, not the cookie — **[+]**

**Spec §1.4:** "Server-side sessions (random 256-bit id in DB…)".

The cookie is still a random 256-bit token, but `sessions.id` holds `sha256(token)`.
A leaked database dump therefore yields no usable session cookies. The column type and
name are unchanged.

`sessions.csrf_token` was added to carry the per-session CSRF secret.

---

## 6. CSRF is enforced with three checks, not one — **[+]**

**Spec §1.4:** double-submit token on HTML forms; `X-Requested-With: GauTrack` on the
JSON API.

Both are implemented. In addition, every state-changing `/api/*` call must present
`X-CSRF-Token` matching the session's token. All three clients (field PWA, dashboard,
public report page) send it. This is strictly stronger than the spec and costs nothing,
since we control every client.

`/api/auth/logout` is deliberately outside the CSRF gate: logging out only ever removes
access, and it must work even after the token is lost.

---

## 7. Login throttling lives in a database table — **[~]**

**Spec §1.4:** "Login rate-limit (5/min/IP + 10/hour/user, lockout 15 min)."

All three limits are implemented exactly. The counters live in a `login_attempts` table
rather than in process memory, so they survive a restart and hold across multiple
workers — in-process counters would be trivially defeated by restarting the container or
by load-balancing across workers. `login_attempts` is one table beyond the spec's §2 list.

Lockout is triggered by `users.failed_logins >= 10` (§7's wording) and lasts 15 minutes
(§1.4's wording); both are configurable.

---

## 8. Content-Security-Policy uses a per-request nonce — **[+]**

Not specified. The dashboard has a few inline `<script>` blocks (chart data), and
`script-src 'self'` alone would silently break them in a real browser. Rather than
weaken the policy with `'unsafe-inline'`, a fresh nonce is minted per request and
attached to each inline block. `style-src` still needs `'unsafe-inline'` because
Leaflet and Chart.js set element styles directly — recorded here as the one remaining
inline-content allowance.

---

## 9. English-first UI (client instruction, mid-build) — **[~]**

**Spec §1.11 / §6** asked for a bilingual field app.

The client changed this during the build to English only. The translation dictionaries
(`api/i18n.py`, `api/static/app/i18n.js`) were kept for a while behind a flag and removed
on 2 September 2026: only seven of their strings were ever rendered, all in English, and
the two hand-mirrored copies were a drift risk. Those strings are now literal text in the
templates and the field app.

---

## 10. Truncating the end of the audit chain is not self-detectable — **[!]**

This is a property of hash chains, not a bug, but it should be understood.

`verify_chain.py` detects any **edit** to a row and any **deletion from the middle** of
the chain. Deleting rows from the **end** leaves a shorter chain that is still internally
consistent, and no self-check can see that.

The countermeasure is `scripts/anchor.py`: publish the day's tip hash somewhere outside
the database (signed e-mail to the DMC and the auditor, the day's file noting, a
printout). Once published, nothing recorded before it can be removed without the
published hash disappearing from the chain.

`tests/test_audit.py::test_truncating_the_tip_is_caught_by_the_published_anchor_not_the_chain`
documents exactly this, and asserts the anchor is what catches it. `harden_vm.sh`
installs a cron entry that runs `anchor.py` daily.

---

## 11. Rows are gap-tolerant by design — **[~]**

`audit_log.id` comes from a sequence, and a rolled-back transaction consumes a number
without leaving a row. So gaps in `audit_log.id` are legitimate and the verifier does
**not** treat them as tampering — it verifies the hash links instead.

---

## 12. Cross-ULB events are allowed; cross-ULB *edits* are not — **[~]**

**Spec §1.5** deliberately lets a field officer look up any animal district-wide,
because a Bawal cow will be found in Rewari. That implies an officer must be able to
record an impound against an out-of-ULB animal.

The rule implemented: an officer may record operational events on an out-of-ULB animal
(`sighting_road`, `impound`, `gaushala_intake`, `release`, `fine_issued`, `fine_paid`,
`tag_lost`, `tag_replaced`, `death`, `note`) but **not** record-rewriting ones
(`correction`, `transfer_owner`, `owner_merge`). See `CROSS_ULB_EVENT_TYPES` in
`api/sync.py`. Without that split, any officer could rewrite any animal in the district.

---

## 13. `POST /api/sync` accepts owners and animals as well as events — **[~]**

**Spec §3** describes `/api/sync` mainly in terms of events. The offline field app has to
create owners and animals offline too, so each queued item carries a `kind`
(`owner` | `animal` | `event`). Idempotency, per-item status and the `≤200` cap work the
same for all three.

---

## 14. `sighting_road` nudges `animals.status` — **[~]**

**Spec §3** lists side effects for `impound`, `gaushala_intake`, `release`,
`fine_issued`, `tag_lost`, `tag_replaced`, `death` and `transfer_owner`, but not for
`sighting_road`. Since `animal_status_enum` contains `on_road_reported` and the dashboard
needs it, a road sighting sets that status — but only from `registered`, `released` or
`tag_missing`, so it can never overwrite `impounded`, `in_gaushala` or `deceased`.

---

## 15. `/cm` requires a login — **[~]**

**Spec §5** calls `/cm` a read-only viewer page and **§1.5** defines a `viewer` role for
"CM/press/public dashboard". Those two pull in opposite directions. `/cm` requires a
session of any role; the seeded `viewer` account exists for exactly this. Publishing it
without authentication is a one-line change once the DMC decides the numbers are public.
`/report` is genuinely public, as specified.

---

## 15a. `viewer` is district-wide for aggregates, blind for records — **[~]**

**Spec §1.5:** "`viewer` (CM/press/public dashboard): aggregate stats only, no PII, no
per-owner pages."

Two different scopes are needed to satisfy that one sentence, and conflating them was a
bug caught during end-to-end verification: the `viewer` role's *record* scope is empty (it
may not read a single owner or animal), and reusing that empty scope for the aggregate
queries made `/cm` render every number as zero.

`authz.Scope` now exposes `ulb_ids` (records) and `stats_ulb_ids` (aggregates). Only
`viewer` differs between them: no record access at all, district-wide totals.
`stats.py` uses `stats_ulb_ids`; everything else uses `ulb_ids`.
`tests/test_authz.py::test_viewer_sees_real_district_wide_numbers` pins this down by
asserting the viewer's totals equal the `super_admin`'s and are non-zero, while
`/api/owners` still returns 403.

---

## 16. Photo downscaling is client-side only — **[!]**

**Spec §1.8** requires the client to downscale to ≤1600 px / ≤400 KB before queueing;
that is implemented in `api/static/app/app.js`. The **server** enforces only the 5 MB cap,
the JPEG/PNG magic-byte check and the SHA-256 match — it does not re-encode.

Consequence: a hostile client can still upload a valid 5 MB JPEG. Disk, not security, is
the exposure. A server-side re-encode (Pillow) is the obvious follow-up and was left out
to avoid adding an image-parsing dependency to the attack surface for the MVP.

---

## 17. Demo icons are generated, not designed — **[~]**

`api/static/app/icon-192.png` / `icon-512.png` are generated by a small script rather
than drawn. They exist so the PWA installs cleanly and the manifest validates. Replace
before any public launch.

---

## 18. `make dev-tls` added for the phone demo — **[+]**

Not in the spec; added on the client's mid-build instruction. Android Chrome blocks
geolocation, camera capture and service-worker registration on a plain-`http` LAN
address. `make dev-tls` runs Caddy with `tls internal` on `:8443` so the demo page is a
secure context. The field app also degrades gracefully without it: GPS shows an explicit
"GPS blocked: this page is not on https" message and saving still works, with the entry
stored without coordinates.

---

## 19. Fine schedule follows Haryana ULB practice, not a notified order — **[!]**

Updated 2026-08-17 after research: seeded as **₹5,100 first offence / ₹11,000
repeat** (Haryana govt press note; Ambala, Faridabad, Rewari reports) with the catching charge
set to the **Rewari 2026 tender rate ₹1,880/animal**, and an FIR flag from the third offence
(Rewari administration announced FIRs on 14 Aug 2026). These are administrative practice under
municipal nuisance powers — no gazette order number was located. The amounts live in the
`fine_schedule` table; **confirm the order number with ULB Haryana before quoting to the CM.**

---

## 20. Not built (out of scope for the MVP window)

- `POST /api/users/{id}/reset_password` returns the new password to the caller and
  revokes that user's sessions; there is no "change your own password" screen yet, so a
  user cannot rotate their temporary password themselves. **Add before real use.**
- TOTP is fully implemented (enrolment URI, verification, per-user flag) but no QR image
  is rendered — the dashboard shows the `otpauth://` URI as text.
- `GET /api/stats/summary` accepts `from`/`to` and validates them, but the date window is
  not yet applied to every sub-query (the 7-day and 30-day windows are fixed).
- No pagination UI on the dashboard lists; they are capped at 200 rows server-side.

---

## 22. Hardening after review round 1 (2026-08-18, migration `0002_council_hardening`)

Applied from the independent spec review (round 1):
- `fine_schedule.authority_ref / legal_status / effective_from` and `fines.authority_ref` — every fine
  now records the legal instrument it rests on at issue time; issuing without a schedule row is refused.
- `lookup_log` — every district-wide tag lookup (hit or miss) is recorded with user, tag, IP; append-only
  and hash-chained through the same audit trigger; per-user cap of 120 lookups/hour (HTTP 429).
- Out-of-ULB lookups now return the owner's **initials** (not full name) and no relation name, in
  addition to the masked phone.
- `occurred_at` more than 6 h in the future is rejected (device-clock gaming).
- Chart canvases wrapped in fixed-height containers (fixes runaway chart height); fine schedule aligned to
  Haryana practice ₹5,100 / ₹11,000 with ₹1,880 catching charge (Rewari 2026 tender rate).
Already present and verified (raised in review, already handled): advisory-lock-serialised hash chains;
orphan photos default-deny (uploader-only); geo points rounded to 3 dp (~110 m); GRANT-level append-only.
Still open (day-2): `tag_assignments` history table; UNIQUE(owner_id, offence_number); DPDP consent
version + retention policy; `shelter_daily_counts`; `contractor_workorders`; anchor tip hash off-box daily.

## 23. iPhone support and UI text (2026-08-18)
- Verified in the iOS Simulator (iPhone 17, Safari) over plain http on the LAN: sign-in, home screen, Register Owner form. Runbook §3 now covers iPhone (certificate profile trust, Add to Home Screen, Safari password prompt, 7-day storage note).
- Fixed: form inputs set to 16px so iOS Safari does not auto-zoom on focus; service-worker cache bumped to v3.
- Field app is English only, per the client (the translation layer itself was removed later; see section 9).
- Replaced em dashes in UI text with colons/hyphens (client style). Seed full_name strings still contain one; reseed to change.

## 24. Photo capture crashed on http (plain LAN address) — fixed, 2026-08-19

**Bug reported by the client**: on step 9 of the runbook (register an owner over plain
`http://<lan-ip>:8000`, no https), attaching a photo failed with
`Could not use that photo: undefined is not an object (evaluating 'crypto.subtle.digest')`,
which blocked the Save button on both Register Owner and Register Animal, and therefore
blocked the offline-mode test that follows it.

**Root cause**: `crypto.subtle` (the browser API used to compute a client-side SHA-256 of
the photo before upload) only exists in a *secure context* (`https://`, or `http://localhost`).
On a LAN address over plain http it is `undefined`, so `crypto.subtle.digest(...)` threw a
TypeError inside `capture()`, which the surrounding `try/catch` turned into the alert the
client saw — before the photo was ever attached to the form.

**Why this was safe to make optional**: the server (`photos.py: store_photo`) always computes
its own SHA-256 of the uploaded bytes and stores that; the client-supplied hash
(`client_sha256`) is only used *if present*, as an extra tamper cross-check
(`routes/photos_routes.py`). Server-side integrity is therefore unaffected by omitting it.

**Fix**: `static/app/app.js` (`sha256Hex`) and `templates/report.html` (`sha256`) now check
`window.crypto && window.crypto.subtle` first and return `null` instead of throwing;
`uploadPhoto()` and the public report page only append the `sha256` form field when a hash
was actually computed. Service worker cache bumped to `v4` so phones pick up the fix.

**Verified** (2026-08-19): reproduced the exact reported error live in a real browser at the
same insecure origin (`http://192.168.1.10:8000`, `isSecureContext: false`,
`crypto.subtle` `undefined`) using the literal old code, got the equivalent TypeError; ran
the patched `sha256Hex` in the same context (`threw: false, result: null`); then drove the
*actual production* `#ow-photo` file input's real `change` handler with a genuine JPEG built
via canvas — no alert fired, the thumbnail rendered, and Save advanced to Register Animal
with the new owner pre-selected, exactly the designed flow. `make test`: 68/68 pass, before
and after.

**One incidental finding while checking demo data counts**: `/api/stats/summary` shows 61
owners instead of the seeded 60 — one extra row, "Test owner" / "Test father", timestamped
2026-08-18 22:13:39, i.e. from earlier interactive testing in this project (not from this
fix's own verification, which was confirmed never to leave its browser tab's local queue).
It was left in place rather than deleted directly by SQL, since that would bypass the
audit/hash-chain the system is designed to make tamper-evident; `make reseed` removes it but
regenerates every password in `.seed_credentials.txt`, so that is left as the client's call.

## 25. Data access, lifecycle commands, runbook restructure (2026-08-19)

Prompted by the client's questions: where does the data live, can an admin download it,
could Power BI point at it, why is this on localhost, and why did stopping the server not
end the browser session.

**New: CSV export** (`api/routes/export_routes.py`, tests in `tests/test_export.py`).
`/api/export/{owners,animals,events,fines,shelters}.csv`, sliceable with `?ulb=`, `?from=`,
`?to=`. Three rules, each tested: only super_admin / ulb_admin / auditor may export (a
field officer may look up any tag but not take the keeper list; `viewer` cannot export
personal records at all); ULB scope applies to files exactly as on screen and a filter
cannot widen it; every download is recorded in the new append-only, hash-chained
`export_log`. Files are UTF-8 with BOM for Excel, and any cell beginning `= + - @` is
prefixed with an apostrophe to defuse spreadsheet formula injection. "Download CSV" links
added to the admin owners/animals/events/fines pages, shown only to roles that may export.

**New: read-only analytics login** (`make analyst-user`, migration
`0003_exports_and_analytics`). Creates `gautrack_ro` with `default_transaction_read_only`,
granted SELECT on operational tables plus a pre-aggregated `v_daily_counts` view, and
explicitly denied `users`, `sessions`, `devices`, `login_attempts`. Verified live: reads
animals and the view; permission denied on users and sessions; UPDATE refused by the
database. Intended for Power BI Desktop / Excel; the runbook warns that publishing to the
Power BI *Service* would upload a copy to Microsoft's cloud and contradicts the proposal's
data-residency position.

**Bug fixed: `make stop` did not stop the web server.** It only stopped the database, so
uvicorn processes accumulated (two were found still listening on :8000 from earlier
sessions, which is why edits appeared not to take effect). `make stop` now stops both;
added `make api-stop` and `make status`.

**Bug fixed: enum columns exported as Python reprs** (`KeeperType.household` instead of
`household`) - caught by the formula-injection test's output, not by the assertion itself.

**Runbook restructured** into: what runs what (a lookup table of the 8 surfaces and which
command starts each) · why it runs on a laptop and the four-stage path to government
hosting · first-time setup (once) · everyday start/stop · phone demo · accounts · demo
script · where data lives and how to get it out · where each dashboard number comes from ·
backups · maintenance and repair · resetting (with the argument that a live registry should
never be reset: correct, merge, restore or archive instead, and the append-only design
means even an administrator cannot erase history). Terminal and expected-output blocks are
now distinguished by colour and a one-word label only.

**Explained, not changed:** stopping the server does not close browser tabs or end
sessions. `Session expired` is the 2-hour admin idle timeout (12 h for field), stored in
the database and therefore surviving restarts - working as designed, unrelated to `stop`.

## 26. Runbook PDF build (2026-08-19)

The printable runbook is generated from `README.md` by `scripts/build_runbook_pdf.py`
(`make runbook`): title page, an index whose page numbers are resolved by rendering,
reading back which page each heading landed on, and re-rendering until stable, clickable
cross-references ("Section 4" in the prose links to Section 4), and a page-number footer.
Em dashes are converted at render time (a lone dash in a table cell is preserved as a
placeholder). Verified: 26 pages, 53 index entries, 69 internal links, index page numbers
spot-checked against the pages the sections actually landed on.

## 27. Technical architecture document (2026-08-19)

`ARCHITECTURE.md` written for engineers and IT staff: request lifecycle, data model rationale,
authentication, the scoped-query authorisation model and why IDOR is structurally impossible,
database privilege separation, the two hash chains (including what they do NOT prove), the
offline-sync contract, photo/export/browser hardening, the test strategy, an honest
twelve-item weakness list, the production delta, a suggested reading order through the code,
and a concept index for self-assessment. Rendered to PDF by the same builder as the runbook
(`make runbook` now produces both); the builder was parameterised via a `DOCS` table.

## 28. Field-survey columns beyond `SPEC.md` §2 (2026-08-29) — **[+]**

**Spec §2** fixes the `owners` and `animals` column lists. Five optional columns were added
on top of them after the first walk-through of the forms, in migration
`0004_field_additions`:

| Column | Table | Why |
|---|---|---|
| `identification_mark_1`, `identification_mark_2` | `animals` | Natural marks (broken horn, torn ear, white sock). The fallback identity when a tag is cut off — which is the tampering mode the council rated most likely. Free text, both optional. |
| `age_years` | `animals` | The keeper's stated age. `age_class` stays the enum the dashboards aggregate on; this is the finer number the office wanted in the CSV. |
| `self_declared_cattle_count` | `owners` | What the keeper says at the door, recorded before anything is counted. The gap against the animals actually registered is the under-declaration signal, and it is surfaced as a red figure on the owners list and on the owner's own page. |
| `premises_area_sq_yards` | `owners` | Shed/plot size in the unit used locally (gaz), not square metres. Feeds the capacity question: how many animals a premises can physically hold. |

Every one is nullable and none is required by any form: an officer standing in a lane must
never be blocked from finishing a registration because a box was empty. Bounds are enforced
twice — Pydantic at the edge for a readable 422, and a `CHECK` constraint in the database so
a bad value cannot arrive by any other route (age 0–40, declared count 0–2000, area
0–1,000,000). All five flow through the offline sync path, the correction path, the API
responses, the admin pages and the CSV exports. Covered by `tests/test_survey_fields.py`.

Two supporting changes were needed:

- **`api/db.py` now sets a `json_serializer` on both engines.** Event payloads carry
  before/after snapshots of table rows, so a `Decimal` reaches a JSONB column as soon as
  `age_years` is corrected. `json.dumps` refuses `Decimal`, `UUID` and `datetime` by default,
  which turned an ordinary correction into a 500. Decimals are written as JSON numbers;
  `fines.amount` has its own `Numeric` column and never round-trips through JSON.
- **`seed.py --force` now truncates `export_log` and `lookup_log`.** Both reference
  `users(id)`, so on any machine where someone had taken a CSV or looked up a tag, a reseed
  failed with a foreign-key violation. Pre-existing since `0003`; found while reseeding.

## 29. Dashboard rework: one line per band, colour discipline, insight cards (2026-08-29) — **[+]**

**Spec §5** describes the dashboard's contents but not its shape. Three changes, all
requested after seeing it on a real screen:

**One line per band.** The nine KPI tiles were on an `auto-fill` grid that wrapped on to two
rows on anything under a very wide monitor, pushing the charts below the fold. Bands are now
fixed-column grids (`--n`) with every size expressed in viewport units through `clamp()`, so
the strip and the chart row each stay on one line from a 1280-wide laptop to a 16:9
boardroom screen. `/admin` scrolls below the charts for the map and detail tables; `/cm` is a
flex column pinned to `100dvh` with `overflow:hidden` — one screen, no scroll, panes
scrolling internally if a district ever has more shelters than fit. Below 1000 px wide (or
560 px tall) `/cm` gives up the single-screen promise and scrolls, because keeping it would
mean crushing every figure into illegibility.

**Colour discipline.** Previously everything was the brand green, so the same colour meant
"tagged", "buffalo", "Bawal" and "doing well" on one screen. Now: a categorical palette with
one hue per chart (the rule that repeated the hue along the top of the card was removed on
2026-08-30, see §31); and green/amber/red
reserved exclusively for percentages, with the direction stated per use (`pct_class(pct,
'high_good' | 'high_bad')` — coverage green at ≥75%, shelter load red at ≥90%). The only
chart that uses green is tagged-vs-untagged by ULB, which is a coverage percentage drawn as
counts.

**Insight cards (`api/static/insight.js`).** Any element carrying `data-insight` becomes
clickable: it detaches, flies to the centre of the screen and enlarges; a button on it turns
it over to a face carrying what the figure counts, the table and condition behind it, the scope applied, the
aggregate endpoint, and a link that opens those rows in the registry. Notes on the build:

- The provenance text is rendered by the server into `data-ins-*` attributes, so the
  explanation cannot drift away from what the query actually does, and nothing is evaluated
  from a string (the CSP forbids inline script).
- Handlers are delegated from `document`, because HTMX re-swaps the KPI strip every 60 s and
  anything bound to a card would be lost on the first refresh.
- The enlarged chart is a real second Chart.js instance built by a factory the chart
  registers (`GTInsight.chart`), not a screenshot — so it keeps its own tooltips. This forced
  the chart option objects in `dashboard.js` to become *functions*: Chart.js caches resolved
  option proxies on the config it is handed, and sharing one `scales.x` literal between two
  instances kills the second with `this._fn is not a function`.
- The open animation starts from a forced reflow rather than `requestAnimationFrame`, which
  is throttled or stopped outright in a backgrounded tab and would strand the card at its
  starting position.
- `#map` is pinned to `z-index:0` with `isolation:isolate`. Leaflet stacks its own panes at
  z-index 400–700; without an isolating context those panes compete at the root and paint the
  map straight over the modal.
- `.card` gained `overflow-x:auto`, so a six-column table in a one-third column scrolls
  inside its own card instead of widening the page.
- Motion is fully suppressed under `prefers-reduced-motion`; cards are keyboard-reachable
  (`tabindex`/`role=button`, Enter or Space to open, Escape to close).

On `/cm` the "open these records" link is rendered only for roles that may read records; the
`viewer` role is offered the aggregate endpoint instead, because that is genuinely all it can
open.

## 30. Passwords made stable across a reseed (2026-08-30) — **[+]**

**Spec §8** says the demo seed writes random credentials to `.seed_credentials.txt`. That is
right for a demo laptop — a box that ships with a guessable password is worse than one whose
password has to be looked up — but it made `make reseed` an event that silently signed
everyone out and invalidated every written-down credential, which a pilot cannot live with.
Three changes, none of which weakens the "no guessable default" property:

- **`make reseed` now keeps existing passwords.** Before the wipe, `seed.py` snapshots
  `username → password_hash` and puts the hash back on the account it recreates. Hashes are
  moved, never decrypted. `make reseed-new-passwords` (or `python -m seed --force
  --new-passwords`) restores the old behaviour when a clean set is genuinely wanted.
- **The credentials file carries forward too.** Keeping the password while rewriting the file
  with blanks would leave the accounts working and the operator with no idea what they are —
  the plaintext exists in that file and nowhere else, by design, since the database holds
  only argon2 hashes. `previously_recorded_passwords()` parses the old file and reprints the
  values for preserved accounts.
- **`make set-password U=<username>`** (`api/set_password.py`) sets one account's password to
  something chosen. The password is read from a hidden prompt, never a command-line argument
  (arguments are visible via `ps` and land in shell history). All existing sessions for that
  account are revoked — a password change that leaves old cookies working has not changed
  anything for an attacker who already has one. The change goes through the schema-owner
  connection so the audit trigger records it. If the account appears in
  `.seed_credentials.txt`, its row there is rewritten to `(set manually)`, because a stale
  password in a credentials file is worse than none: it walks the reader into the ten-attempt
  lockout.
- **`SEED_PASSWORD`** in `.env` (default empty) gives every *newly created* seeded account a
  fixed password, so a pilot box that gets reseeded repeatedly has reproducible credentials.
  Empty means random, which stays the default.

Verified by fingerprinting `md5(password_hash)` for `dmc` and `field1` either side of a
`make reseed` (identical), by logging in with the pre-reseed password afterwards (200), and
by setting a password with `set_password()` and logging in with it after a further reseed (200).

**Not fixed, and now listed in `ARCHITECTURE.md` §12 as item 4b:** an officer still cannot
change their own password. `make set-password` serves the operator at the server; it does
nothing for the officer in the field who thinks their password has been seen.

## 31. Dashboard corrections after review (2026-08-30) — **[+]**

Seven changes, all from a review of the built screens rather than from the spec. Recorded
here because three of them reverse decisions taken six days earlier in §29.

**The enlarged card opened on the wrong face — a real bug.** `openCard()` in
`api/static/insight.js` added `flipped` to the modal in the same frame it set the final
geometry, so the card flew to the centre and immediately turned over. The reader clicked a
figure and landed on the *explanation*, never seeing the enlarged figure at all; the button
that was supposed to bring them back to the card read as doing nothing, because there was
nothing recognisable to come back to. The `classList.add("flipped")` is gone, so the modal
now opens on the enlarged card. The buttons were relabelled to say where they lead:
**"Where does this number come from?"** on the card, **"Back to the card"** on the
explanation.

Two supporting fixes, because the flip had a second failure mode that was browser-dependent
and would have been miserable to diagnose later. `backface-visibility:hidden` was the only
thing hiding the away-facing side, and each face is itself a scroll container
(`overflow:auto`); a scrolling element that faces away is still painted by some engines and,
worse, still receives the clicks aimed at the face in front of it. The away-facing side is
now explicitly `visibility:hidden; pointer-events:none`, switched on a `0s` transition
delayed to `.26s` — half of the `.52s` turn — so the swap happens while the card is edge-on
and is never seen. The `prefers-reduced-motion` block was inverted to match the new
front-first order.

**The coloured rule along the top of each card is gone.** §29 introduced `border-top:3px
solid var(--tint)` so a card announced its chart's hue. In use it read as decoration applied
to a government document. Colour still carries meaning, but only inside the charts, where a
hue is attached to an actual series. Every `style="--tint:…"` attribute was removed from
`cm.html`, `admin/_kpis.html` and `admin/overview.html` (27 in total), along with the
matching CSS rule and the tinted left border on the provenance block.

**Uppercasing removed from headings and table headers.** `text-transform:uppercase` on `h2`,
`th`, `.zoom-kicker` and `.zoom-src dt` destroyed the capitalisation that distinguishes a
place name from an initialism, and made "Registrations per day (30 days)" harder to scan,
not easier. Weight and colour carry the hierarchy instead.

**Three list cards now scroll internally.** Shelter occupancy, the field-team leaderboard
and the repeat-offenders table are all lists that grow with the district. A new `.tscroll`
container caps them at `clamp(190px,27vh,340px)` with a sticky header row, so a card keeps
its height and the band stays on one line however many rows exist.

**The "By ULB" table was removed from `/admin`.** The pilot runs at municipal-committee
level only, so a per-ULB breakdown would be an empty table on a live screen. On `/cm` the
equivalent card was kept but relabelled from "Progress by urban local body" to "Progress by
municipal committee", with its column header and provenance text changed to match — the
wording now follows the data rather than the other way round. The "Animals by ULB" *chart*
on `/admin` was left in place pending a decision: it is meaningful with three committees in
the demo data and degenerate with one.

**The species chart is a pie, not a doughnut.** Reported as "half a pie". Measured at four
viewport sizes, the ring was geometrically complete every time (`outerRadius` 87 against a
176 px chart area, and so on), so the hollow centre was reading as a missing piece rather
than as a style. `cutout` is gone and the type is `pie`; a percentage was added to the
tooltip, which is what the hole was failing to earn.

**`/cm` spacing and layout.** The gap between the headline strip and the band below it was
`clamp(.45rem,.6vw,1rem)` — the same gap used between two cards inside one band, which made
two different kinds of information look like one. It is now `clamp(1rem,2.4vh,2.4rem)`, and
the tagging bar sits `.55rem` under its own figure. The bottom band became a two-row grid:
shelter capacity and committee progress side by side, the hour chart across the full width
beneath (`.cm-fill > .card.wide`). The single-screen promise still holds — verified at
1600×900 with `scrollHeight − clientHeight === 0`.

**Static assets are now cache-busted.** `asset_version()` in `api/routes/pages.py` computes
a hex stamp from the newest modification time under `api/static/` (excluding `vendor/`,
whose version is already in each filename) and exposes it as the Jinja global `asset_v`;
every stylesheet and script tag carries `?v={{ asset_v }}`. This was not cosmetic: during
this very session an edited `admin.css` kept serving from cache through repeated reloads,
which on a deployed machine is indistinguishable from "the fix did not work". The stamp is
computed once at import, so **restarting the web server is what publishes a front-end
change** — documented in README §11.

All 88 tests still pass.

---

## 32. Handover cleanup (2026-09-02) — **[~]**

Done before the code was handed to the municipal office's developer. Nothing here changes
behaviour a user can see; the test suite (88 cases) passed unchanged afterwards.

- Removed code with no callers: the `AuditLog` ORM model (the audit log is read through
  SQL on purpose), `parse_uuid`, `FineOut`, `session_scope`, `optional_principal`,
  `new_user_id`, `settings.site_address`, a no-op `tag_type` validator, the unused
  `from`/`to` query parameters on `/api/stats/summary`, and the `/api/auth/me` alias of
  `/api/me`.
- The login response is now the same `MeOut` shape as `/api/me` instead of a hand-built
  copy of it (the unused `expires_at` field went with it).
- The translation layer was removed; see section 9.
- `seed.py` uses `schemas.normalize_phone` and `sync.phone_hash` instead of private copies.
- `Makefile`: `dev` and `run` share one server line; new `audit` target (`pip-audit` +
  `bandit`).
- Security review findings: Caddy now caps request bodies at 10 MB; two integer fields in
  the field app are HTML-escaped like every other field; the comment describing
  `phone_hash` as an HMAC was wrong and is corrected; the five gaps the review found that
  are not fixed are listed as items 13 to 18 of `ARCHITECTURE.md` section 12.
- Documents no longer carry machine-specific paths or the names of the tools used to
  write them.
