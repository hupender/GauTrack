# GauTrack MVP — Build Spec (v0.1, 2026-08-17)

Status: v0.1 build specification, 17 August 2026. Every departure from it is recorded in DEVIATIONS.md.

## 0. One-paragraph purpose
Registry + field tool + live dashboard for stray/road cattle accountability in **Rewari district, Haryana** (ULBs:
Rewari MC, Bawal MC, Dharuhera MC, + "Rural/Other" bucket). Field teams register **owners** and their **animals**
(tag ID, photo, GPS), record **events** (road sighting, impound, release, fine, gaushala intake, tag lost/replaced,
death, transfer). The DMC sees a **live dashboard**. Everything self-hosted, no third-party SaaS, no client-side trust.

## 1. Non-negotiable architecture decisions (do not deviate)
1. **Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.x (sync engine is fine) · Alembic migrations · PostgreSQL 16 ·
   Jinja2 + HTMX for admin dashboard · a small **vanilla-JS PWA** for the field app (no React/Next) · Caddy for TLS.
   Everything runs with `docker compose up` (services: `db`, `api`, `caddy`). Dev mode also runnable without Caddy.
2. **No external services at runtime.** No CDNs (vendor Chart.js, Leaflet, HTMX into `static/vendor/` — download once
   at build time with pinned versions and record SHA-256 in `static/vendor/CHECKSUMS.txt`). Map tiles: OSM tile URL
   is allowed **only** behind a config flag `MAP_TILES_URL` (default OSM; production note: replace with self-hosted
   PMTiles or NIC Bharat Maps). No Firebase, no Sentry SaaS, no Google fonts, no analytics.
3. **DB is never reachable from the client.** Only the API talks to Postgres. Postgres port not published to host in
   prod compose. Photos stored on a Docker volume (`/data/photos`), served **only** through an authenticated API route.
4. **Auth:** username + password (argon2id). Server-side sessions (random 256-bit id in DB, httpOnly+Secure+SameSite=Lax
   cookie), 12h idle expiry for field, 2h for admin. Login rate-limit (5/min/IP + 10/hour/user, lockout 15 min).
   Optional TOTP for admin roles (implement; enable via user flag). CSRF: double-submit token on all state-changing
   HTML forms; JSON API requires `X-Requested-With: GauTrack` header + same-site cookie (reject otherwise).
5. **AuthZ is enforced server-side on every query** via a `scope` object derived from the session:
   - `super_admin` (DMC office): all ULBs, all actions, user management, merges.
   - `ulb_admin`: own ULB — read/write owners/animals/events, issue fines, manage gaushala counts; no user mgmt.
   - `field_officer`: assigned ULB — create owners/animals/events; **read any animal by tag district-wide** (a cow from
     Bawal will be found in Rewari — deliberate), sees owner name + ward + phone-masked for out-of-ULB owners.
   - `viewer` (CM/press/public dashboard): aggregate stats only, no PII, no per-owner pages.
   - `auditor`: read-only everything incl. audit log.
   Every object route MUST load the row through a scoped query (`.where(ulb_id.in_(scope.ulb_ids))`), never by
   raw id alone. Write tests that prove IDOR is impossible (see §7).
6. **IDs:** UUIDv7 everywhere (unguessable *and* authz-checked). Client generates the UUID for offline-created rows.
7. **Append-only events + tamper-evident audit:**
   - `events` table is append-only (no UPDATE/DELETE grants for the app role; enforce with a trigger that raises).
   - `audit_log` written by DB triggers on INSERT/UPDATE/DELETE of owners/animals/users/fines/gaushalas, storing
     before/after JSON, actor (`SET LOCAL app.user_id`), ip, and a **hash chain**: `hash = sha256(prev_hash || row_json)`.
   - `scripts/verify_chain.py` walks the chain and prints OK/BROKEN + first broken id. `scripts/anchor.py` prints
     the day's tip hash (to be emailed/printed daily — out of scope to send).
   - Photos: SHA-256 computed server-side on upload and stored; client also sends its hash; mismatch → reject.
8. **Offline-first field PWA:** IndexedDB queue of pending mutations; each mutation carries client-generated `id`,
   `device_id`, `occurred_at`; POST `/api/sync` accepts a batch and is **idempotent** (INSERT … ON CONFLICT (id) DO NOTHING,
   returns per-item status: `created|duplicate|conflict|rejected` with reason). Client keeps items until server acks.
   Service worker caches app shell. Show a sync badge (pending count). GPS via `navigator.geolocation` with accuracy
   stored. Photos captured via `<input type=file accept=image capture=environment>`, downscaled client-side to ≤1600px
   JPEG ≤ 400KB before queueing.
9. **Conflict rules (server is the arbiter):**
   - `animals.tag_id` UNIQUE (partial index where tag_id not null). Second registration of the same tag → `conflict`,
     response includes existing animal summary; client offers "record a sighting/event on the existing animal instead".
   - Two field users creating the *same owner* twice: no auto-merge. Server returns `possible_duplicates` (same
     normalized phone, or name+village trigram similarity > 0.6) at create-time as a warning; `super_admin` has a
     merge tool (`owners.merged_into`, all animals/events re-pointed, merge recorded as an event + audit row).
   - Corrections never overwrite: a `correction` event with payload; materialized current fields on `animals` updated
     by the API in the same transaction (audit trigger captures before/after).
10. **Privacy minimization:** store phone (needed to notify) and address; **do not store Aadhaar numbers**; optional
    `id_type` + `id_last4` only. Phone masked in lists for non-admin roles. Photos of animals, not people, by default.
11. **Bilingual UI:** English + Hindi labels for field-app buttons/fields (simple dict in `i18n.py`; Hindi text as given
    in §6). Admin dashboard English.
12. **Secrets & config:** all in `.env` (`.env.example` committed). `SECRET_KEY`, `POSTGRES_PASSWORD`, `SITE_ADDRESS`,
    `MAP_TILES_URL`, `PHOTO_DIR`, `SEED_DEMO=1`. Nothing secret in the repo. Compose runs API as non-root user.

## 2. Data model (Postgres; Alembic migration `0001_init`)
- `ulbs(id, code, name, district='Rewari', lat, lng)` — seed: RWR (Rewari MC), BWL (Bawal MC), DHR (Dharuhera MC), RUR (Rural/Other).
- `users(id, username UNIQUE, password_hash, full_name, role ENUM, ulb_id NULL, phone, is_active, totp_secret NULL, failed_logins, locked_until, created_at)`
- `sessions(id TEXT PK, user_id, created_at, expires_at, ip, ua, revoked_at NULL)`
- `devices(id UUID, user_id, label, registered_at, last_seen_at, last_ip)`
- `owners(id UUID, ulb_id, name, relation_name, phone_norm, phone_hash, address, ward_or_village, keeper_type ENUM(household,dairy_tabela,commercial,gaushala,trader,other), id_type NULL, id_last4 NULL, self_declared_cattle_count NULL, premises_area_sq_yards NULL, lat, lng, gps_accuracy_m, photo_id NULL, notes, merged_into NULL, created_by, created_at, updated_at)`
- `animals(id UUID, ulb_id, owner_id NULL, species ENUM(cattle,buffalo), sex ENUM(male,female,unknown), age_class ENUM(calf,young,adult,old), age_years NULL, breed, colour_markings, identification_mark_1 NULL, identification_mark_2 NULL, tag_id NULL, tag_type ENUM(pashu_aadhaar_12,rfid_lf,rfid_uhf,visual,microchip,none), secondary_tag_id NULL, status ENUM(registered,on_road_reported,impounded,in_gaushala,released,transferred,deceased,tag_missing), current_shelter_id NULL, photo_id NULL, muzzle_photo_id NULL, lat, lng, created_by, created_at, updated_at)` + partial unique index on tag_id.
- `shelters(id, ulb_id, name, kind ENUM(gaushala,nandishala,cattle_pound), capacity, current_count, lat, lng, phone)`
- `photos(id UUID, sha256, mime, bytes, path, taken_at, lat, lng, uploaded_by, created_at)`
- `events(id UUID PK client-generated, type ENUM(registration,tagging,tag_lost,tag_replaced,sighting_road,impound,release,fine_issued,fine_paid,gaushala_intake,transfer_owner,death,correction,owner_merge,note), animal_id NULL, owner_id NULL, ulb_id, user_id, device_id NULL, lat, lng, gps_accuracy_m, occurred_at, received_at DEFAULT now(), payload JSONB, photo_ids UUID[], prev_hash, hash)` — append-only.
- `fines(id UUID, event_id, animal_id, owner_id, ulb_id, offence_number INT, amount NUMERIC, status ENUM(issued,paid,waived,contested), receipt_no, issued_at, paid_at)`
- `audit_log(id BIGSERIAL, ts, actor_user_id, ip, action, table_name, row_id, before JSONB, after JSONB, prev_hash, hash)`
- Fine schedule config table `fine_schedule(offence_number, amount)` seeded: 1→₹5,100 (matches Haryana practice, plus
  ₹2,000 catching charge as separate line), 2→₹10,000, 3→₹15,000 + FIR flag (**placeholder — DMC to confirm legal schedule**).

## 3. API (JSON, `/api/...`, all behind session unless noted)
- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me`
- `GET /api/lookup/tag/{tag_id}` — district-wide, returns animal + owner summary (phone masked unless in-scope admin) + last 5 events.
- `GET/POST /api/owners`, `GET/PATCH /api/owners/{id}` (scoped), `GET /api/owners/{id}/animals`, `POST /api/owners/{id}/merge` (super_admin).
- `GET/POST /api/animals`, `GET/PATCH /api/animals/{id}` (scoped), `GET /api/animals/{id}/events`.
- `POST /api/events` (single) and `POST /api/sync` (batch, idempotent, ≤200 items) — event side-effects: `impound`→animal.status=impounded; `gaushala_intake`→in_gaushala + shelter count; `release`→released; `fine_issued`→creates fine row w/ offence_number = count(prior fines for owner)+1; `tag_lost`→tag_missing; `tag_replaced`→update tag_id (old tag preserved in payload); `death`→deceased; `transfer_owner`→owner_id changed.
- `POST /api/photos` (multipart; ≤5MB; JPEG/PNG sniffed by magic bytes; returns id+sha256), `GET /api/photos/{id}` (scoped through the entity that references it; viewer role denied).
- `GET /api/stats/summary?ulb=&from=&to=`, `GET /api/stats/timeseries`, `GET /api/stats/by_ulb`, `GET /api/stats/repeat_offenders`, `GET /api/stats/shelters`, `GET /api/stats/sightings_geo` (aggregated points, no PII) — `viewer` allowed on stats.
- `GET /api/audit?table=&row_id=` (auditor/super_admin), `GET /api/audit/verify` runs chain verification.
- Admin: `GET/POST /api/users` (super_admin), `POST /api/users/{id}/reset_password`, `POST /api/users/{id}/toggle`.
- Public (no session, rate-limited 10/hour/IP, honeypot field): `POST /api/public/report` (photo + GPS + optional tag digits) → event type `sighting_road` with `user_id=NULL`, `payload.source='public'`. Page at `/report`.

## 4. Field PWA (`/app`)
Screens: Login → Home (big buttons: **Register Owner / मालिक पंजीकरण**, **Register Animal / पशु पंजीकरण**,
**Animal on Road / सड़क पर पशु**, **Impound / जब्त करें**, **Lookup Tag / टैग खोजें**, **Sync (n pending)**).
- Register Owner: name, relation name (S/o, W/o), phone, ward/village, ULB (locked to user's ULB), keeper type, address,
  photo (optional), GPS auto (show accuracy; allow retry), notes → save (offline ok) → go to "add animal to this owner".
- Register Animal: pick owner (recent / search by phone or name; offline search over locally cached recent owners),
  species, sex, age class, breed (free text w/ suggestions: Sahiwal, HF cross, Jersey cross, Murrah, Desi/Non-descript),
  colour/markings, tag type + tag ID (numeric keypad; 12-digit validation for pashu_aadhaar; also accept scanner
  keyboard-wedge input), photo (required), muzzle close-up (optional), GPS → save.
- Animal on Road: tag ID → lookup (online) or type "no tag" → photo (required) + GPS + notes → action: **Warn owner /
  Impound / Fine** → creates `sighting_road` (+ `impound`/`fine_issued`).
- Impound untagged: quick stray registration (species/sex/age/photo/GPS) with `owner_id=NULL`, status impounded,
  choose shelter → `impound` + `gaushala_intake` events.
- Lookup Tag: shows animal card, owner, offence count, event timeline.
- Sync: shows queue, per-item status, retry, errors (conflict shows existing record).
Manifest + service worker (app-shell cache; API calls network-only). Works on Android Chrome.

## 5. Admin dashboard (`/admin`, Jinja+HTMX, Chart.js, Leaflet)
- Overview: KPI tiles (owners, animals, tagged %, tagged today, on-road sightings 7d, impounded now, in gaushala,
  fines issued/collected ₹, repeat offenders); charts: registrations/day (line, 30d), animals by ULB (bar), species/sex
  (donut), sightings by hour-of-day (bar), map of sightings (last 30d, clustered), shelter occupancy (bars w/ capacity),
  field-team leaderboard (registrations by user, today/7d), top-10 repeat-offender owners (name masked for viewer).
- **Layout: one line per band.** The nine KPI tiles occupy a single row and the four charts a single row, sized in
  viewport units (`clamp()`), so on a 16:9 screen no band wraps on to a second line and the charts stay above the
  fold. The page scrolls for the map and the detail tables below.
- **Colour discipline.** Each chart owns one hue from a categorical palette and repeats it as a rule along the top of
  its card; green/amber/red are reserved for percentages alone (coverage: green ≥ 75%, amber ≥ 40%, red below;
  shelter load reverses the direction, red ≥ 90%).
- **Insight cards.** Clicking any KPI or chart lifts it to the centre of the screen, enlarges it and turns it over to
  a face giving what the figure counts, the table and condition it came from, the scope applied, and a link that opens
  those rows in the registry (plus the aggregate JSON endpoint). Charts are rebuilt live at full size, not screenshotted.
- Filters: ULB, date range. Auto-refresh every 60s (HTMX polling).
- Owners list/detail (animals + events), Animals list/detail (timeline + photos), Events feed, Fines list,
  Shelters, Users (super_admin), Audit log + "Verify chain" button.
- `/cm` — read-only viewer page (aggregates only). **One screen, no scrolling**: banner, six KPIs on one line, then a
  band that takes the remaining height and splits it three ways. Sized for a projector; panes scroll internally if a
  district ever has more shelters than fit. Registry links appear only for roles allowed to read records.

## 6. Hindi labels (use exactly)
मालिक पंजीकरण (Register Owner) · पशु पंजीकरण (Register Animal) · सड़क पर पशु (Animal on Road) · जब्त करें (Impound) ·
टैग खोजें (Lookup Tag) · सिंक (Sync) · नाम (Name) · पिता/पति का नाम (Father's/Husband's name) · मोबाइल (Phone) ·
वार्ड/गाँव (Ward/Village) · पता (Address) · गाय (Cow) · भैंस (Buffalo) · नर (Male) · मादा (Female) · बछड़ा/बछड़ी (Calf) ·
टैग नंबर (Tag number) · फोटो लें (Take photo) · सहेजें (Save) · चेतावनी (Warn) · जुर्माना (Fine) · गौशाला (Gaushala).

## 7. Tests (pytest, run in CI-less `make test`) — must pass
- Unauthenticated → 401 on every non-public route.
- `field_officer` of RWR cannot GET/PATCH owner/animal of BWL by id (404, not 403 — don't leak existence) but CAN
  `GET /api/lookup/tag/{tag}` for a BWL animal (phone masked).
- `viewer` cannot fetch owners/animals/photos; can fetch stats.
- Duplicate `POST /api/sync` batch → all `duplicate`, no double rows; same tag by two users → `conflict`.
- Photo hash mismatch → 400; non-image → 400.
- Audit chain verifies OK after seed; tampering a row directly via SQL → verify reports BROKEN.
- Login lockout after 10 failures.

## 8. Seed (`SEED_DEMO=1`, clearly labelled DEMO in UI banner)
4 ULBs, 4 shelters (names: "Shri Krishna Gaushala (demo)", etc.), users: `dmc`/`admin` (super_admin), `rwr_admin`,
`bwl_admin`, `field1..field6` (2 per ULB), `viewer`, `auditor` — passwords printed by seed script (random, written to
`mvp/.seed_credentials.txt`, git-ignored). 60 owners, 240 animals (70% tagged; 60% cattle/40% buffalo; realistic
Rewari-area names/villages: Kosli, Bawal, Dharuhera, Masani, Kund, Jatusana, Nahar), 45 days of events incl. ~120
road sightings clustered along NH-48/NH-11 near Rewari (lat 28.19, lng 76.62 ± noise), 30 impounds, 18 fines
(some repeat offenders), 6 tag_lost. Sighting timestamps skew to 05:00-09:00 and 17:00-21:00.

## 9. Repo layout
```
mvp/
  README.md            # run, deploy, hardening checklist, backup/restore, threat model summary
  docker-compose.yml   # db, api, caddy
  docker-compose.dev.yml
  Caddyfile
  .env.example
  Makefile             # dev, test, seed, migrate, backup, verify-chain
  api/                 # FastAPI app: main.py, config.py, db.py, models.py, schemas.py, auth.py, authz.py, routes/*.py,
                       # i18n.py, seed.py, stats.py, sync.py, photos.py, audit.py
  api/alembic/
  api/static/vendor/   # chart.js, leaflet, htmx (+CHECKSUMS.txt)
  api/static/          # admin.css, admin.js, dashboard.js (overview charts + map),
                       # cm.js (CM chart), insight.js (click-to-flip provenance cards)
  api/static/app/      # PWA: index.html, app.js, sw.js, manifest.json, styles.css, i18n.js
  api/templates/       # Jinja: admin/*.html, cm.html, report.html
  scripts/backup.sh, restore.sh, verify_chain.py, anchor.py, harden_vm.sh
  tests/
```

## 10. Deploy notes to include in README
Ubuntu 24.04 VM (Mumbai/Delhi region), 2–4 vCPU/8GB; `harden_vm.sh` (ufw allow 22,80,443; ssh key-only; fail2ban;
unattended-upgrades; docker); set `SITE_ADDRESS=cattle.example.gov.in`; Caddy auto-TLS; nightly `backup.sh`
(pg_dump | age-encrypt → `/backups` + rsync to second host); restore drill documented; log rotation; how to migrate
to HARTRON/State Data Centre (it's just the compose stack + a volume). CERT-In audit checklist stub.

## 11. Definition of done
`make dev` → http://localhost:8000/admin works with seed; `/app` PWA works on phone via LAN (offline queue tested by
toggling airplane mode); `make test` green; `python scripts/verify_chain.py` OK; README complete.
