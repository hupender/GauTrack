# GauTrack — Technical Architecture

**Audience:** a software engineer or IT officer who has to review, extend, audit or take
over this system. It explains *why* each decision was made, not only what the code does,
and it is honest about what is unfinished. The plain-English operational guide is
[README.md](README.md); this document assumes you can read Python and SQL.

**Reading it as a checklist:** each section ends with *Concepts used*, naming the technique
so you can look it up if it is unfamiliar. That list is the fastest map of what this
codebase assumes you know.

---

## 1. The shape of the thing

One Python process serving four surfaces from one database. There is no message queue, no
cache tier, no microservice, and no background worker.

```
                    ┌──────────────── one FastAPI process ────────────────┐
  field phone ──────▶ /app      static PWA  (offline queue in IndexedDB)  │
  DMC browser ──────▶ /admin    Jinja + HTMX server-rendered              │
  CM / press  ──────▶ /cm       aggregates only, no PII                   │
  public      ──────▶ /report   unauthenticated, rate-limited             │
  scripts     ──────▶ /api/*    JSON, session-authenticated               │
                    └───────────────────────┬─────────────────────────────┘
                                            │  two roles, two connection pools
                                            ▼
                                      PostgreSQL 16
                                   (registry + audit chains)
                                            │
                       photos on local disk, content-addressed
```

**Why this and not something more modern.** The constraint driving every choice is that
this has to be handed to a district IT department and survive a CERT-In audit, on hardware
they control, possibly maintained by someone who did not write it. Boring, few moving
parts, and inspectable beats elegant. Concretely:

- **Server-rendered HTML for the dashboard, not a SPA.** No build step, no npm tree, no
  bundle to audit. `view-source` shows what runs. HTMX gives partial refresh without a
  framework.
- **No JWT.** Sessions are rows. A row can be revoked instantly; a signed self-contained
  token cannot be, short of a blocklist that reintroduces the database lookup you were
  avoiding. Revocation matters when a field phone is lost.
- **No ORM migrations magic beyond Alembic**, and the security-critical DDL (triggers,
  grants) is written as raw SQL in the migration so a reviewer reads exactly what runs.
- **Postgres does the security-critical work** (append-only enforcement, audit chaining,
  privilege separation) because application code can be bypassed by whoever has a database
  client, and trigger-enforced invariants cannot.

*Concepts used: server-side rendering, HTMX, session vs token authentication, defence in
depth.*

---

## 2. Stack, and the reason for each piece

| Layer | Choice | Why this one |
|---|---|---|
| HTTP | FastAPI (ASGI, uvicorn) | Dependency injection is what makes "every route passes one auth gate" enforceable; Pydantic gives request validation for free |
| ORM | SQLAlchemy 2.x, sync | Sync is fine at this scale, and sync stack traces are debuggable by a maintainer who is not an async expert |
| Migrations | Alembic | Reviewable, ordered, reversible DDL. Security DDL lives here on purpose |
| DB | PostgreSQL 16 | Triggers, `SECURITY DEFINER`, advisory locks, partial unique indexes, `pg_trgm`, JSONB. Nothing here works on SQLite |
| Templates | Jinja2 + HTMX | Partial page updates without client state |
| Field app | Vanilla JS + IndexedDB + service worker | Zero dependencies to audit or update; works on any modern phone browser |
| Passwords | argon2id (`argon2-cffi`) | Memory-hard; OWASP baseline parameters |
| TLS | Caddy | Automatic certificates; in production may be replaced by the department's reverse proxy |

**Version pinning.** `api/requirements.txt` pins exact versions; front-end libraries
(Chart.js, Leaflet, HTMX) are vendored into `api/static/vendor/` with SHA-256 checksums
recorded, so there is no CDN in the serving path and no silent upstream change.

*Concepts used: ASGI, dependency injection, memory-hard password hashing, supply-chain
pinning / SRI-equivalent.*

---

## 3. One request, end to end

Tracing `PATCH /api/owners/{id}` is the fastest way to understand the whole system.

1. **`security_headers` middleware** (`main.py`) generates a per-request CSP nonce, stores
   it on `request.state`, and sets CSP, `X-Content-Type-Options`, `X-Frame-Options`,
   `Referrer-Policy`, `Permissions-Policy` and (when TLS is on) HSTS on the way out.
2. **`get_db`** yields one SQLAlchemy `Session`, i.e. one transaction, for the request.
3. **`get_principal`** (`auth.py`) is the single gate:
   - reads the `gt_session` cookie, hashes it, looks up the session row;
   - rejects if missing, revoked, expired, or the user is inactive;
   - slides the idle expiry (writing at most once a minute, so reads do not cause a write
     per request);
   - calls `set_db_actor`, which issues `set_config('app.user_id', …, true)` — the
     parameterised form of `SET LOCAL`, scoped to this transaction, so a pooled connection
     cannot leak one request's identity into the next;
   - for unsafe methods, requires both `X-Requested-With: GauTrack` and a matching
     `X-CSRF-Token`;
   - builds a `Scope` from the user's role.
4. **The route** builds a *scoped* SELECT via `apply_ulb_scope` and loads the row. An
   out-of-scope id returns no row, so the handler raises 404.
5. **The write** flushes; Postgres fires `gt_audit()` on the row change, which reads
   `app.user_id` back out of the transaction and appends a hash-chained audit row.
6. **Commit** happens at the end of the request; the audit entry and the data change are in
   the same transaction, so an audit record can never be missing for a committed write.

The load-bearing idea is step 5: attribution is passed to the database as transaction state,
so the audit trail is written by Postgres rather than by application code that could forget.

*Concepts used: ASGI middleware, request-scoped transactions, `SET LOCAL` / `set_config`,
trigger-based auditing, connection pooling hazards.*

---

## 4. Data model

Twelve tables. The ones that matter:

| Table | Role | Notes |
|---|---|---|
| `ulbs` | Urban local bodies | The scoping axis for the whole system |
| `users` | Accounts | `role` enum, optional `ulb_id`, argon2 hash, optional TOTP |
| `sessions` | Live logins | PK is the **SHA-256 of the cookie token**, not the token |
| `owners` | Cattle keepers | `phone_norm` + keyed `phone_hash`; trigram index for fuzzy duplicate search |
| `animals` | Animals | Partial unique index on `tag_id WHERE tag_id IS NOT NULL` |
| `events` | Append-only history | `seq BIGSERIAL`, `prev_hash`, `hash`, JSONB `payload` |
| `fines` | Money | Carries `authority_ref`, the legal instrument, copied at issue time |
| `photos` | Image metadata | Content-addressed; bytes on disk, never in the DB |
| `audit_log` | Every row change | Hash-chained, append-only, written only by trigger |
| `lookup_log`, `export_log` | Sensitive reads | Tag lookups and bulk exports, also chained |

**Design decisions worth understanding:**

- **Events are the history; the row is a projection.** `animals.status` is a materialised
  convenience. The truth is the event stream. A correction is a new `correction` event, not
  an overwrite, so a fine can be defended years later.
- **Partial unique index on `tag_id`.** A tag number must be unique among animals that have
  one, but many animals have none. `UNIQUE(tag_id)` would allow only one untagged animal
  (in Postgres NULLs are distinct, so it would actually work — but the partial index states
  the intent explicitly and keeps the index small).
- **`phone_norm` and `phone_hash` both exist.** The normalised number is needed to call the
  keeper; the hash is an HMAC keyed with `SECRET_KEY`, used for duplicate detection. A
  plain SHA-256 of a ten-digit Indian mobile is brute-forceable in seconds, which is why it
  is keyed. *Known wart:* storing both weakens the argument for the hash; see §12.
- **`id_last4` with a CHECK constraint**, never a full Aadhaar number. Mandatory Aadhaar
  needs a law made by Parliament, and a municipal bye-law is not one.
- **Survey columns are optional in the schema, and that is a decision, not an oversight.**
  `animals.identification_mark_1/2`, `animals.age_years`,
  `owners.self_declared_cattle_count` and `owners.premises_area_sq_yards` are all nullable
  (migration `0004`). A required field on a form held by an officer standing in a lane with
  an uncooperative keeper does not produce better data; it produces a plausible invention,
  or an abandoned registration. Ranges are bounded by CHECK constraints (age 0–40, declared
  count 0–2000, area 0–1,000,000) so that a typo cannot enter the register, and the same
  bounds are stated in Pydantic so the caller gets a readable 422 rather than a 500 out of
  Postgres.
- **`self_declared_cattle_count` is a claim, not a count, and is stored as one.** It is what
  the keeper says before anything is counted. Its value is precisely the arithmetic against
  the animals actually registered: the gap is where the next visit goes. Storing it in the
  same row as the verified data, clearly named, keeps that distinction visible to everyone
  who reads the table.
- **Identification marks are the fallback identity.** The review panel rated tag removal the most
  likely form of tampering. A cut tag leaves the muzzle photograph and these two free-text
  marks, which is why they are carried on every animal API response and shown on a tag
  lookup in the field app.
- **UUIDv7 primary keys** (`ids.py`): time-ordered, so B-tree inserts stay local rather than
  scattering like UUIDv4, with 74 random bits so ids are not enumerable. Unguessability is
  explicitly *defence in depth only* — every route still scopes its query.

*Concepts used: event sourcing (partial), materialised projections, partial indexes,
keyed hashing / HMAC, UUIDv7 vs v4 index locality, GIN + `pg_trgm`.*

---

## 5. Authentication

`api/auth.py`.

- **argon2id**, 64 MiB memory / 3 passes / 4 lanes. Memory-hard, so GPU cracking is
  expensive.
- **Session token**: 256 bits from `secrets.token_hex(32)`. The database stores
  `sha256(token)` as the primary key. A dump of the `sessions` table therefore does not let
  anyone log in as anybody — the same reason you hash passwords, applied to session tokens.
- **Idle expiry**: 12 h field, 2 h admin, sliding. The window is deliberately shorter for
  the roles that can see personal data across a ULB.
- **Throttling**: 5 attempts/min/IP, 10/hour/username, and a 10-failure lockout for 15
  minutes. Attempts are rows in `login_attempts`, so the limits survive a restart and work
  across workers — an in-process counter would do neither.
- **Timing**: when the username does not exist, the code verifies the password against a
  fixed dummy argon2 hash anyway, so "no such user" and "wrong password" cost the same. Without
  it, response time enumerates valid usernames.
- **`X-Forwarded-For` is trusted only `TRUSTED_PROXY_COUNT` hops deep.** Blindly trusting
  the header lets anyone spoof an IP and walk around the per-IP limit. Set this to the real
  number of reverse proxies in front, and no higher.
- **TOTP** is implemented and per-user; making it mandatory for admin roles is on the
  pre-production list (§12).

*Concepts used: argon2id, token hashing at rest, sliding expiry, persistent rate limiting,
timing-attack mitigation, proxy header spoofing.*

---

## 6. Authorisation, and why IDOR is structurally impossible

`api/authz.py`. This is the part worth reading closely, because it is where most
"vibe-coded" systems fail.

The rule is: **a handler never loads a row by primary key alone.** It builds a statement and
passes it through `apply_ulb_scope`, which appends the ULB restriction derived from the
session. An out-of-scope id therefore does not merely fail an `if` — it matches no row.

```python
def apply_ulb_scope(stmt, column, scope):
    if scope.ulb_ids is None:          # district-wide role
        return stmt
    if not scope.ulb_ids:              # empty scope must match nothing
        return stmt.where(column.in_([-1]))   # IN () is invalid SQL
    return stmt.where(column.in_(scope.ulb_ids))
```

Three details that are easy to get wrong and are handled deliberately:

1. **Empty scope must match nothing, not everything.** `viewer` has `ulb_ids = ()`. A naive
   implementation skips the filter when the list is empty and hands over the whole table.
   The `in_([-1])` guard makes the empty case fail closed.
2. **Out-of-scope returns 404, never 403.** A 403 confirms the record exists, which is
   itself a disclosure. Tested explicitly.
3. **Aggregates are a separate axis.** `stats_ulb_ids` deliberately returns district-wide
   for `viewer`, because the CM/press screen must show the whole district while that same
   role may not read a single owner. Conflating "may read records" with "may see counts"
   would either leak PII or produce an empty dashboard — an earlier bug did exactly the
   latter until the two were split.

**Field-level masking** is a third layer: `mask_phone` and `mask_name` apply when a field
officer looks up a tag belonging to another ULB. The district-wide tag lookup exists on
purpose (a Bawal cow is found in Rewari), so it is rate-limited to 120/hour/user and every
lookup — hit or miss — is written to `lookup_log`.

*Concepts used: IDOR, fail-closed defaults, 403-vs-404 information leakage, capability
scoping, field-level redaction.*

---

## 7. Privilege separation inside the database

Application-layer authorisation protects the application. It does nothing against someone
with a `psql` prompt, or against a remote-code-execution bug. So the database enforces the
invariants itself.

Three roles:

| Role | Rights | Used by |
|---|---|---|
| `gautrack_owner` | Owns the schema; DDL | Alembic migrations, seeding, chain verification |
| `gautrack_app` | DML on operational tables; **no UPDATE/DELETE on `events`**; **SELECT only on `audit_log`** | The web process |
| `gautrack_ro` | SELECT on operational tables; `default_transaction_read_only` | Power BI / Excel / analysts |

The web process's credentials genuinely cannot rewrite history. The grant is verified in the
test suite via `has_table_privilege`, not merely assumed.

On top of the grants, `gt_append_only()` is a **statement-level** BEFORE trigger on `events`
and `audit_log` that raises unconditionally, so even a zero-row `UPDATE` is refused, and the
table owner does not silently bypass it.

The audit trigger itself is `SECURITY DEFINER`, i.e. it runs with the schema owner's rights.
That is what lets a role with no INSERT on `audit_log` still cause audit rows to be written,
while being unable to write them directly or choose their contents.

*Concepts used: least privilege, role separation, `SECURITY DEFINER`, statement-level vs
row-level triggers, privilege verification in tests.*

---

## 8. Tamper evidence: two hash chains

There are two independent chains: `audit_log` (ordered by `id`) and `events` (ordered by
`seq`). Each row stores `prev_hash` and `hash`, where

```
hash = sha256(prev_hash || canonical_json(row))
```

Editing any historical row invalidates every hash after it, so undetected tampering requires
rewriting the entire tail.

Three implementation details carry the whole guarantee:

1. **Canonical serialisation is defined once, in SQL** (`gt_audit_payload`,
   `gt_event_payload`), and used by both the writer (the trigger) and the reader
   (`scripts/verify_chain.py` via `api/audit.py`). If writer and verifier serialised
   independently — different timestamp precision, different key order — the chain would
   report BROKEN on honest data, and everyone would learn to ignore it. Timestamps are
   forced to UTC with microsecond precision for the same reason.
2. **A chain needs a total order**, so both triggers take `pg_advisory_xact_lock` (7301 for
   audit, 7302 for events) before reading the tip. Without it, two concurrent transactions
   read the same `prev_hash` and fork the chain, producing spurious BROKEN under ordinary
   load. This is the single most common way a homegrown hash chain fails in practice.
3. **Credentials are stripped before hashing**: the trigger removes `password_hash` and
   `totp_secret` from the `users` payload, so the audit log never becomes a second copy of
   the password database.

**What the chain does not prove, stated plainly.** It detects modification of *history*. It
cannot detect deletion of the **tip** — the most recent rows — because there is nothing after
them to break. Anyone with full database control can also recompute the whole chain. The
countermeasure is to publish the tip hash off the machine daily (`make anchor`), so an
external record exists of what the chain looked like. Until that anchoring is actually
running, the chain is protection against a careless or dishonest *user*, not against a
compromised *server*. `scripts/verify_chain.py` returns exit 0 OK / 1 BROKEN / 2 could-not-check,
and the honest limitation is documented in the test suite rather than glossed over.

*Concepts used: hash chaining, canonical serialisation, advisory locks, tip-anchoring /
notarisation, threat model boundaries.*

---

## 9. Offline sync

The field app must work with no network, and phones do not have reliable clocks or reliable
connections. `api/sync.py` and `api/static/app/app.js`.

**Client.** Every entry is written to an IndexedDB `queue` store with a
**client-generated UUIDv7** and marked `pending`. A sync attempt uploads photos first (a
queued row may reference a photo not yet on the server), then POSTs up to 200 items to
`/api/sync`. An item is only marked `done` when the server acknowledges it *by id*; no ack
means it stays queued. Nothing is deleted optimistically.

**Server.** `process_batch` wraps **each item in its own SAVEPOINT** (`db.begin_nested()`),
so one malformed row cannot roll back the good ones in the same batch. Each item resolves to
exactly one of four outcomes:

| Status | Meaning | Client behaviour |
|---|---|---|
| `created` | Accepted | mark done |
| `duplicate` | This id already exists — a replay | mark done (idempotent) |
| `conflict` | Business collision, e.g. tag already registered | surface, with the existing record |
| `rejected` | Invalid or not permitted | surface the reason |

**Idempotency** comes from the client-generated id: a replayed batch finds the row already
present and returns `duplicate` rather than creating a second copy. This is why the client,
not the server, mints ids — the client must be able to retry safely without knowing whether
its previous attempt landed.

**Clock skew.** `occurred_at` comes from the phone and a phone's clock is settable, so a
timestamp more than six hours in the future is rejected. Otherwise offence windows and
"last 7 days" counts could be gamed by changing the device clock.

**Duplicate owners are flagged, never auto-merged.** `find_possible_duplicates` uses exact
`phone_norm` match plus `pg_trgm` similarity > 0.6 on name + village, backed by a GIN index.
Auto-merging two keepers because their names are similar would corrupt the ownership record
that fines depend on; only a super_admin merges, and the merge is itself audited.

**Event side effects** are centralised in `_apply_side_effects`: `impound` sets status,
`gaushala_intake` increments the shelter count, `tag_replaced` preserves the old tag in the
payload and rejects a clash, `sighting_road` deliberately never overwrites a terminal state
such as `deceased`. Keeping these in one function is what stops the projection drifting from
the event stream.

**Known weakness:** `duplicate` is decided by id alone, so a replay carrying a *modified*
payload under a reused id is accepted as a duplicate and silently ignored. Comparing a
content hash would close that. Listed in §12.

*Concepts used: idempotency keys, client-generated ids, SAVEPOINT / nested transactions,
last-writer semantics, trigram similarity, clock-skew defence.*

---

## 10. Photos, exports, and the browser

**Photos** (`api/photos.py`). Type is decided by **magic bytes**, never the `Content-Type`
header or the filename. Files are **content-addressed**: stored at
`{sha[0:2]}/{sha[2:4]}/{sha}.jpg`, written to a `.part` file and then `os.replace`d, which is
atomic on POSIX, so a crash mid-write cannot leave a torn file at the final path. Identical
images deduplicate for free. Nothing is served from a static directory: `GET /api/photos/{id}`
re-derives access by asking whether the caller can see *any* entity referencing that photo,
in one `EXISTS` query. *Gap:* EXIF is not yet stripped, so a served JPEG can still carry the
capture location (§12).

**Exports** (`api/routes/export_routes.py`). Bulk PII extraction is treated as a privileged
act: only super_admin / ulb_admin / auditor; scope applies to files exactly as on screen;
filters can narrow but never widen; every download is recorded in the append-only
`export_log`. Cells beginning `= + - @` are prefixed with an apostrophe to defuse
**CSV formula injection** (a keeper named `=HYPERLINK(...)` would otherwise execute in the
clerk's spreadsheet). Output is UTF-8 **with BOM** so Excel does not mangle Hindi names or ₹.

**Browser hardening** (`main.py`). CSP is `default-src 'self'` with a **per-request nonce**
for inline scripts, so injected markup cannot execute without guessing the nonce.
`style-src` still allows `'unsafe-inline'` because Leaflet and Chart.js set inline styles —
an honest, documented compromise, not an oversight. The only external origin permitted is
the map tile host, and only for images. `frame-ancestors 'none'`, `base-uri 'none'`,
`object-src 'none'`. Interactive API docs (`/docs`, `/openapi.json`) are disabled: free
attack surface in a government deployment.

**CSRF is three overlapping defences**: `SameSite=Lax` on the session cookie, a required
`X-Requested-With: GauTrack` header (a cross-site HTML form cannot set headers), and a CSRF
token compared with `hmac.compare_digest`. The login form, being a real `<form>`, uses
classic double-submit instead.

**Service worker** (`sw.js`) caches the **app shell only**. API responses are deliberately
**network-only**: a stale cached answer about who owns an animal is worse than no answer, and
the offline write path is the IndexedDB queue, not the HTTP cache.

**Secure context.** `crypto.subtle`, geolocation and the service worker only exist on HTTPS
or `localhost`. Over plain `http://<LAN-IP>` they are absent — which is why photo hashing
falls back to skipping the client-side hash rather than throwing (the server hashes
authoritatively regardless), and why GPS degrades with an explanatory message instead of
failing the save.

**The dashboard front end** is Jinja + HTMX + two small vanilla-JS files, and three
decisions in it are worth knowing before changing anything:

- **Layout is fixed-column, viewport-sized.** Each band of the dashboard is a grid with an
  explicit column count (`--n`) and every size expressed through `clamp()` in `vw`/`vh`. That
  is what keeps nine figures and four charts each on a single line from a 1280-wide laptop up
  to a 16:9 screen. `/cm` goes further: a flex column pinned to `100dvh` with
  `overflow:hidden`, so it is one screen with no scrolling, panes scrolling internally
  instead. Under 1000 px wide it deliberately gives that up and scrolls.
- **Colour carries exactly one meaning.** Green/amber/red are percentages and nothing else,
  assigned server-side by `pct_class(pct, direction)` — because the same 95% is excellent for
  tagging coverage and an emergency for shelter occupancy, and the reader should not have to
  work out which way round a bar is. Every other colour is a categorical label: one hue per
  chart, repeated as a rule on that chart's card.
- **Insight cards** (`static/insight.js`) turn any `data-insight` element over to show where
  its number came from, with a link into the rows. The provenance text is rendered by the
  server into `data-ins-*` attributes, so it cannot drift from the query and nothing is
  evaluated from a string under the CSP. Handlers are delegated from `document` because HTMX
  re-swaps the KPI strip every 60 s. The enlarged chart is a genuine second Chart.js instance
  built from a factory the chart registers — which is why `dashboard.js` builds its option
  objects in *functions*: Chart.js caches resolved option proxies on the config it is given,
  and sharing one between two instances breaks the second.

*Concepts used: magic-byte sniffing, content-addressed storage, atomic rename, CSP nonces,
CSRF triple defence, CSV injection, secure contexts, cache strategy choice, fluid typography
with `clamp()`, stacking contexts and z-index isolation, event delegation across DOM swaps,
`prefers-reduced-motion`.*

---

## 11. Tests, and what they are for

`make test` runs 88 tests against a **throwaway database** (`gautrack_test`), created and
migrated fresh, leaving demo data alone. They are not coverage theatre; each file pins one
security property that a future change could silently break:

| File | Property under test |
|---|---|
| `test_auth.py` | 401 on every non-public route; lockout after 10 failures; CSRF rejection |
| `test_authz.py` | 404 (not 403) across ULBs; masked cross-ULB lookup; viewer blocked from records but not stats; lookup rate limit and logging |
| `test_sync.py` | Replayed batch yields `duplicate` with no double rows; same tag from two officers yields `conflict`; fine schedule; **grants verified via `has_table_privilege`** (the app role genuinely lacks UPDATE/DELETE on `events`) |
| `test_photos.py` | Hash mismatch and non-image rejected |
| `test_audit.py` | Chain verifies OK; direct SQL tampering reports BROKEN; append-only refuses DELETE; credentials never enter the audit log |
| `test_export.py` | Export roles; scope cannot be widened; formula injection neutralised; export log append-only |
| `test_survey_fields.py` | The optional survey columns stay optional (blank arrives as `null`, never `0`); bounds refuse a year typed into an age box; marks survive a tag lookup; a `Decimal` correction round-trips through the event payload's JSONB; the CSV headers carry every new column |

The audit test is the interesting one: it *performs* the attack (edits a row via the owner
role) and asserts the verifier catches it. A tamper-evidence claim that is never tested is a
claim, not a control.

*Concepts used: test databases, negative/adversarial testing, property-focused test design.*

---

## 12. Known weaknesses (the honest list)

Ordered by what I would fix first. This is the section to read before extending anything.

**Before real data goes in**
1. **Chain tip is not anchored off-box.** Until `make anchor` output actually leaves the
   machine daily, the chain does not constrain a compromised server (§8).
2. **No `consent_records`.** DPDP expects the notice version and acknowledgement to be
   recorded at registration; there is no retention/purge job either.
3. **EXIF not stripped** from served photos — location metadata leaks beyond the coordinates
   the registry deliberately rounds.
4. **TOTP is optional.** It should be mandatory for `super_admin` and `auditor`.
4b. **No self-service password change.** An officer cannot change their own password; only a
   `super_admin` can reset it, and the reset issues a random temporary one. So a password an
   officer believes has been seen by someone else can only be changed by finding the DMC,
   which in practice means it does not get changed. `POST /api/users/me/password` taking the
   current password and a new one, with all other sessions revoked, is the missing piece.
   (`make set-password` covers the operator standing at the server; it does not cover the
   officer standing in a field.)
5. **Docker compose stack is written but never executed** — no Docker on the build machine.
   Treat the first `docker compose up` as untested.

**Correctness**
6. **Sync replay compares ids only**, not content: a modified payload under a reused id is
   swallowed as `duplicate` (§9).
7. **`offence_number` is computed as `count(prior fines) + 1` at write time.** Two
   concurrent fines for the same owner can both compute the same number. Needs
   `UNIQUE(owner_id, offence_number)` with a retry, or derivation in a view.
8. **No `tag_assignments` history table.** "Tag X was on animal A until March, then animal
   B" is not queryable; the old tag survives only inside an event payload's JSONB. For a
   system whose central claim is tag-to-owner traceability, this is the most substantive
   modelling gap.
9. **No fines appeal workflow.** `fines.status` has `contested` with no route, evidence
   attachment or decision record.

**Defence in depth**
10. **No row-level security.** The scoped-query discipline is enforced by convention and
    tests, not by the database. RLS on `owners`/`animals`/`events` would make a future
    careless route fail closed.
11. **`phone_norm` and `phone_hash` coexist**, which undercuts the point of the hash (§4).
12. **Rate limiting is per-row-count in Postgres** — correct and durable, but it is a write
    per attempt; at much higher volume it needs a different store.

**Found in the security review of 2 September 2026**
13. **No absolute session lifetime.** Expiry is idle-based only (12 h field, 2 h admin) and
    refreshed on use, so a session used every day never expires. A hard cap (say 30 days)
    belongs in `auth.create_session`.
14. **Authentication is per-route by convention.** Every route declares
    `Depends(get_principal)` itself; there is no default-deny middleware. A new route that
    forgets the dependency is open, and only the hardcoded URL list in `tests/test_auth.py`
    would notice.
15. **Anonymous photo uploads can fill the disk.** `POST /api/public/photo` accepts 5 MB per
    request at 10 per hour per IP, and orphaned public photos are never deleted. Needs a
    cleanup job and a per-day cap.
16. **TOTP secrets are stored in plaintext** in `users.totp_secret`, and an accepted code is
    not remembered, so it could be replayed inside its 30-second window.
17. **The per-IP and per-user login throttles are switched off in the test suite**
    (`tests/conftest.py`), so only the lockout counter is exercised by tests.
18. **The application itself has no request-body size limit.** Caddy caps requests at 10 MB
    (`request_body` in the Caddyfile); behind any other proxy the cap must be set there.

*Items 1 to 12 are also listed in `DEVIATIONS.md` with the reasoning at the time. None of
these are hidden in commit messages.*

---

## 13. Production delta

What changes between the laptop and a government deployment — the software does not.

| Concern | Laptop now | Production |
|---|---|---|
| TLS | `tls internal` self-signed | Real certificate, or the department's reverse proxy |
| `COOKIE_SECURE` | 0 for plain-http LAN demo | **1**, plus HSTS (already emitted when set) |
| `TRUSTED_PROXY_COUNT` | 0 | Exact number of proxies in front, no higher |
| Database | Local cluster in `.pgdata` | Managed instance, not exposed publicly |
| Secrets | Generated into `.env` | Injected by the platform; `.env` mode 600 at minimum |
| Map tiles | OpenStreetMap | Self-hosted tiles or NIC Bharat Maps |
| Backups | `make backup` on demand | Nightly, encrypted, off-site, restore drilled quarterly |
| Audit | Verified manually | `verify-chain` on a schedule; tip anchored off-box |
| Demo data | `SEED_DEMO=1` | **0**, and `.seed_credentials.txt` deleted |
| Sign-off | — | CERT-In empanelled auditor VAPT before go-live |

`harden_vm.sh` covers the host layer: ufw, key-only SSH, fail2ban, unattended upgrades.

---

## 14. Where to start reading

In this order:

1. `api/authz.py` — the security model in 160 lines. Everything else assumes it.
2. `api/auth.py` — sessions, CSRF, throttling.
3. `api/alembic/versions/0001_init.py` — schema, triggers, grants. The `CHAIN` string is the
   tamper-evidence design.
4. `api/sync.py` — the offline contract and every event side effect.
5. `api/main.py` — middleware, CSP, router wiring.
6. `api/static/app/app.js` — the client queue, from `enqueue` to `syncNow`.
7. `tests/` — read these as the specification; they state the intended invariants.

`SPEC.md` records what was specified before implementation, and `DEVIATIONS.md` every place
reality departed from it, with reasons. Read both if you are auditing rather than extending:
the gap between them is where the interesting decisions are.

---

## 15. Concept index

Terms this codebase assumes, gathered for quick self-assessment. If any is unfamiliar, that
is precisely where to read next.

**Web/security:** IDOR · CSRF (SameSite, double-submit, custom-header defence) · CSP and
nonces · XSS and `httponly` · secure context · HSTS · clickjacking / `frame-ancestors` ·
CSV formula injection · timing attacks · proxy header spoofing · fail-closed design ·
403-vs-404 disclosure.

**Auth:** argon2id and memory-hard hashing · session vs JWT trade-offs · token hashing at
rest · sliding expiry · TOTP · account lockout vs rate limiting.

**Database:** least privilege and role separation · `SECURITY DEFINER` · statement-level vs
row-level triggers · advisory locks · `SET LOCAL` / `set_config` and pooling · partial
unique indexes · GIN and `pg_trgm` · JSONB · SAVEPOINT / nested transactions · read-only
transactions · connection pooling.

**Distributed/offline:** idempotency keys · client-generated ids · at-least-once delivery ·
conflict taxonomy · clock skew · eventual consistency between an event log and its
projection.

**Data integrity:** hash chaining · canonical serialisation · notarisation/anchoring ·
append-only stores · event sourcing and projections · content-addressed storage · atomic
rename.

**Frontend:** progressive web apps · service worker caching strategies · IndexedDB ·
`crypto.subtle` · HTMX partial rendering.

**Process:** migrations as reviewable DDL · adversarial testing · threat modelling ·
dependency pinning · VAPT / CERT-In empanelment · DPDP obligations.
