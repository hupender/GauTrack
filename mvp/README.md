# GauTrack — Rewari district stray-cattle registry (MVP)

A working registry for stray and road cattle in **Rewari district, Haryana**: a phone app
for field teams, a live dashboard for the District Municipal Commissioner, a read-only
screen for the Chief Minister, and a public "there is a cow on the road" form.

Everything runs on **one machine you control**. No third-party cloud service, no external
account, no vendor lock-in. Moving it to HARTRON or the State Data Centre later is a copy
of this folder plus one database dump.

> This document is written to be read by people who do not write software. Technical terms
> are used properly — you will meet them again in every vendor conversation and every
> audit — and each one is explained the first time it appears. There is a
> [Glossary](#glossary) at the end.

---

## Contents


**Understand it**
1. [What runs what: the parts of GauTrack](#1-what-runs-what-the-parts-of-gautrack)
2. [Where this can run, and what each place is for](#2-where-this-can-run-and-what-each-place-is-for)

**Use it**
3. [First-time setup (once ever, on this Mac)](#3-first-time-setup-once-ever-on-this-mac)
4. [Everyday use: starting and stopping](#4-everyday-use-starting-and-stopping)
5. [Showing the field app on a phone (iPhone or Android)](#5-showing-the-field-app-on-a-phone-iphone-or-android)
6. [Accounts, and what each role can see](#6-accounts-and-what-each-role-can-see)
7. [What to click during a demo](#7-what-to-click-during-a-demo)

**The data**
8. [Where the data lives, and how to get it out](#8-where-the-data-lives-and-how-to-get-it-out)
9. [Where the dashboard's numbers come from](#9-where-the-dashboards-numbers-come-from)
10. [Backups, and the restore drill](#10-backups-and-the-restore-drill)

**Keep it running**
11. [Maintenance and repair](#11-maintenance-and-repair)
12. [Resetting: what it destroys, and why you would almost never do it](#12-resetting-what-it-destroys-and-why-you-would-almost-never-do-it)
13. [Running the tests](#13-running-the-tests)
14. [Proving the data has not been tampered with](#14-proving-the-data-has-not-been-tampered-with)

**Take it to production**
15. [Showing it live over the internet, before there is a server](#15-showing-it-live-over-the-internet-before-there-is-a-server)
16. [Deploying to a real server](#16-deploying-to-a-real-server)
17. [Hardening checklist](#17-hardening-checklist)
18. [Threat model, in plain terms](#18-threat-model-in-plain-terms)
19. [Moving to HARTRON / State Data Centre / NIC](#19-moving-to-hartron--state-data-centre--nic)
20. [CERT-In audit checklist (stub)](#20-cert-in-audit-checklist-stub)

**Reference**
21. [Repository layout](#21-repository-layout)
22. [Every `make` command](#22-every-make-command)
23. [Known gaps](#23-known-gaps)
23. [Glossary](#glossary)

---

## 1. What runs what: the parts of GauTrack

> **For engineers and IT staff:** [ARCHITECTURE.md](ARCHITECTURE.md) is the technical
> companion to this document. It explains the request lifecycle, the authorisation and
> tamper-evidence design, the offline-sync contract, and an honest list of what is still
> unfinished. This runbook tells you how to run it; that one tells you why it works.


GauTrack is **one program** (a web server) talking to **one database**. Everything below is
a different *page* served by that same program, not a separate application to install or
start. That is why a single `make dev` brings the whole thing up.

| # | Surface | What it is | Who uses it | Address | Started by |
|---|---|---|---|---|---|
| 1 | **Field app** | Phone web app: register owners and animals, record road sightings, impound, look up a tag. Works offline. | Field officers, catching squads | `/app` | `make dev` |
| 2 | **Admin dashboard** | Live numbers, charts, map, owner/animal/event/fine registers, user management, audit log | DMC office, ULB admins | `/admin` | `make dev` |
| 3 | **CM view** | Read-only aggregate screen. No names, no photos. Safe to project or share. | CM office, press | `/cm` | `make dev` |
| 4 | **Public report** | "There is a cow on the road" form. No login. Photo + location. | General public | `/report` | `make dev` |
| 5 | **CSV export** | Downloads of the raw rows, sliced by ULB and date | DMC office, auditor | `/api/export/…` | `make dev` |
| 6 | **The database** | PostgreSQL. The actual registry: every owner, animal, event, fine, photo reference and audit record | Nobody directly; the program talks to it | port `55432` | `make dev` (or `make db-up`) |
| 7 | **HTTPS front door** | Optional. Only needed so a *phone* can use GPS and the camera. | Field demo on a phone | port `8443` | `make dev-tls` (separate) |
| 8 | **Analytics login** | A read-only database user for Power BI / Excel | Analyst in the office | port `55432` | `make analyst-user` (once) |

> **One page for everyone else:** [ACCESS_SHEET.html](ACCESS_SHEET.html) is a printable
> sheet showing who needs what — field officers, the DMC office, the CM office and the
> public — with the address each of them opens. Open it in a browser and print it. It
> describes the live system, not this laptop, and it is the page to hand to somebody who
> only needs to know what to ask for.

**The one thing worth remembering:** items 1 to 6 are all started and stopped together.
Item 7 is a separate command you only run for a phone demo. Item 8 is created once and then
just exists.

So:

- `make dev` = start the database + the web server (which serves surfaces 1 to 5).
- `make stop` = stop the web server **and** the database.
- `make status` = tell me what is currently running.

---

## 2. Where this can run, and what each place is for


This is worth understanding properly, because it is the question a Chief Minister's office
or an IT department will ask, and the answer is deliberate rather than accidental.

**`localhost` means "this machine and nothing else".** When the address bar says
`http://localhost:8000`, the browser is talking to a program running on the same computer.
Nothing is on the internet. Nobody else can reach it. That is exactly what you want for a
demonstration: no hosting bill, no security exposure, no permission from anyone, nothing to
break in front of an audience because someone else's network went down.

**A phone on the same Wi-Fi is the second step.** `http://192.168.1.10:8000` is still not
the internet: `192.168.x.x` is a private address that only exists inside your own Wi-Fi.
The phone can reach the Mac because they are on the same network. Still nothing public.

**An address on the internet is the third step, and it is what a demo actually needs.** A
laptop can only show the software to people standing next to it. The system is four
different people on four different devices, and showing *that* means the program has to sit
somewhere all four can reach. Section 15 does this without buying anything, using demo data.

**Real data is the fourth step, and it is a decision, not a technicality.** The moment this
holds real keepers' names and phone numbers, it stops being a demo and becomes a government
registry, which brings obligations: it must sit on Indian government-approved
infrastructure, pass a CERT-In security audit, have a named officer responsible for the
data, and have backups that someone actually tests. Sections 16 to 20 cover that path.

The deliberate sequence, and why each step exists:

| Stage | Where it runs | Reachable by | What it is for | What it needs |
|---|---|---|---|---|
| **1. Laptop** | Your Mac | You, on this Mac | Check a change; develop | Nothing |
| **2. Laptop + phone** | Your Mac | Any phone on the same Wi-Fi | The field workflow, on a real phone | Same Wi-Fi, `make dev-tls` |
| **3. Codespace demo** | A Linux machine GitHub starts from the repository | Anyone you give the address to, on any network | Show the *whole system*: four people, four devices, one database. Demo data only | A GitHub account. **Section 15** |
| **4. Pilot server** | One India-region virtual machine, department account | Named officers over the internet, password-protected | First real data: the repeat offenders, the city cattle sheds | A VM, a domain name, HTTPS. **Section 16** |
| **5. Government hosting** | HARTRON / State Data Centre / NIC | The department | The registry of record for the district | CERT-In audit, a named data officer, formal backups. **Section 19** |

Stage 3 is the one that changes the conversation, because it is the first stage where the
demo is not a story about what the system would do. It is four people using it at once.

**Nothing about the software changes between these stages.** The same folder, the same
commands, the same database. What changes is which machine it is on and who is allowed to
reach it. That is the point of building it this way: the demo is not a throwaway mock-up
that has to be rebuilt for production.

**Which stage should you be at?** Stage 1 while building. Stage 3 for any demonstration
with more than one person in it — the phone-and-dashboard moment is the demonstration, and
it does not work from a laptop. Stages 4 and 5 begin when the department decides to enter
real keepers, and not one day before: the obligations that arrive with real personal data
are the department's to accept, not yours to trigger by accident.

---

## 3. First-time setup (once ever, on this Mac)


Do this section **once**. After that, skip straight to Section 4 every time. The commands
assume the repository was cloned to `~/gautrack`; change that part of the path if it lives
somewhere else.

**Step 1. Install the two programs GauTrack needs.**

Terminal

```bash
brew install postgresql@16 python@3.12
```

If the Mac replies `brew: command not found`, install Homebrew first from https://brew.sh
(one command on that page), then run the box above again. If Docker Desktop is installed
and running, you can skip this step entirely — GauTrack will use Docker instead.

**Step 2. Optional, only if you want to demo on a phone with working GPS.**

Terminal

```bash
brew install caddy
```

**Step 3. Build and start it for the first time.**

Terminal

```bash
cd ~/gautrack/mvp && make dev
```

The first run takes about half a minute, because it is doing several things once:
building a private Python environment, generating random passwords into `mvp/.env`,
creating a private database inside `mvp/.pgdata`, creating the tables, loading the demo
data, and starting the web server.

Expected output

```text
  admin dashboard : http://localhost:8000/admin
  field PWA       : http://localhost:8000/app
  CM view         : http://localhost:8000/cm
  public report   : http://localhost:8000/report
  credentials     : /Users/you/gautrack/mvp/.seed_credentials.txt

INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

**Step 4. Read the passwords.** They were generated randomly on this machine and exist
nowhere else. Open a **second** Terminal window (Cmd-N).

Terminal

```bash
cat ~/gautrack/mvp/.seed_credentials.txt
```

Expected output

```text
username     password                     role           ulb
dmc          Nahar-9fab789b-9450          super_admin    -
rwr_admin    Rewari-17b450bd-8851         ulb_admin      RWR
field1       Kosli-cbc72278-1402          field_officer  RWR
viewer       Kosli-12267ec4-7641          viewer         -
auditor      Kosli-be7f393b-1513          auditor        -
```

**Step 5. Confirm it works.** Open `http://localhost:8000/admin` in Safari or Chrome on the
Mac and sign in as `dmc`. You should see the District overview with numbers and charts.

Setup is done. You never need Section 3 again on this machine.

---

## 4. Everyday use: starting and stopping


This is the whole of normal operation. Two commands.

### Starting

Terminal

```bash
cd ~/gautrack/mvp && make dev
```

The same command as first time, but now it takes a few seconds instead of half a minute,
because the environment and database already exist. It does **not** wipe anything and does
**not** re-create the demo data — your existing records are still there.

Leave this Terminal window open. The server runs inside it. Then open
`http://localhost:8000/admin`.

### Checking what is running

Terminal

```bash
cd ~/gautrack/mvp && make status
```

Expected output

```text
web server (:8000) : RUNNING (pid 81438 81440)
database          : RUNNING
```

### Stopping

Terminal

```bash
cd ~/gautrack/mvp && make stop
```

This stops **both** the web server and the database. Your data is untouched and will be
there when you next run `make dev`.

> **Why the browser still showed a page after you stopped it.** Stopping the server does
> not close browser tabs, and it does not log you out. Two separate things are happening:
>
> - The **page you are looking at** was already downloaded to the browser. It stays on
>   screen like any other page until you reload it. Reload after stopping and you will get
>   "cannot connect" — that is the server actually being off.
> - **`Session expired, please try again`** is not related to stopping at all. Admin logins
>   expire after **2 hours** of no activity (field logins after 12 hours) as a security
>   measure, and the login record lives in the database, so it survives restarts. Seeing
>   that message means your session sat idle too long, not that anything is broken. Sign in
>   again.
>
> Pressing `Ctrl-C` in the server's window also stops the web server, but leaves the
> database running. `make stop` is the clean way to stop everything.

---

## 5. Showing the field app on a phone (iPhone or Android)


The field app is a *progressive web app* (see glossary): a website the phone can add to its
home screen, which keeps working with no network. There is nothing to install from an app
store. It works the same in **Safari on iPhone** and **Chrome on Android**.

The phone and the Mac must be on the **same Wi-Fi**.

### On the Mac, first

**Step 1. Make sure GauTrack is running** (Section 4), then find the address the phone
needs.

Terminal

```bash
cd ~/gautrack/mvp && make lan-ip
```

Expected output

```text
  This laptop's LAN address: 192.168.1.10

  On the phone (same Wi-Fi):
    field app     http://192.168.1.10:8000/app/
    admin         http://192.168.1.10:8000/admin
    with HTTPS    https://192.168.1.10:8443/app/
```

Use your own number wherever `192.168.1.10` appears below.

**Step 2. Let the phone reach the Mac.** Open **System Settings > Network > Firewall**. If
it is Off, nothing to do. If it is On, click **Options...** and either allow incoming
connections for **python3**, or turn the firewall off for the length of the demo. This is
the most common reason a phone sees nothing at all.

### On the phone

**Step 3. Open the field app.**

Open in Safari on the iPhone: `http://192.168.1.10:8000/app/` — type `http://`, not
`https://`, and keep the `:8000`.

You should see a green page titled **GauTrack, Rewari district field app** with a Sign in
form. If Safari says "cannot open the page" or hangs, see Section 11.

**Step 4. Sign in** as `field1` with the password from Section 3 (Step 4). If Safari offers
to save the password, tap **Not Now**.

**Step 5. Register an owner.** Tap **Register Owner**, enter a name and phone, tap the
photo button and take any picture, tap **Save**. A red line will say *GPS blocked: this
page is not on https* — that is expected on a plain `http` address, and the entry saves
anyway, without coordinates. Steps 7 to 9 below fix GPS if you want it.

**Step 6. Show the offline case.** Turn on **Aeroplane Mode**, register another owner, and
watch the counter in the top right go up. Close Safari fully and reopen the address: the
entries are still there, held on the phone. Turn Aeroplane Mode off and the counter drops
to 0 as they upload. Tap **Sync** to see each one marked done.

That is the field workflow. The rest of this section is optional.

### Optional: making GPS work on the phone

Phones only allow a web page to read location on an `https://` address. The demo can serve
`https` using a certificate it issues itself, which the phone must be told once to trust.

**Step 7 (Mac).** In a second Terminal window, with `make dev` still running in the first:

Terminal

```bash
cd ~/gautrack/mvp && make dev-tls
```

**Step 8 (iPhone). Download and trust the certificate.**

Open in Safari on the iPhone: `http://192.168.1.10:8000/static/rootca.crt`

Safari asks whether to allow a configuration profile — tap **Allow**, then **Close**. Then:

1. Open **Settings**. Tap **Profile Downloaded** near the top (if absent: Settings >
   General > VPN & Device Management > Caddy Local Authority).
2. Tap **Install**, enter the passcode, tap **Install** again, then **Done**. iOS warns
   that the profile is unverified; that is expected for a self-issued demo certificate.
3. Go to **Settings > General > About > Certificate Trust Settings** and switch on
   **Caddy Local Authority**. Confirm **Continue**.

**Step 9 (iPhone). Open the secure address.**

Open in Safari on the iPhone: `https://192.168.1.10:8443/app/`

GPS, camera and offline caching now all work. To keep it like an app, tap **Share** then
**Add to Home Screen**, and sign in once more from the icon (the home-screen copy keeps its
own login, and unlike a plain Safari tab its stored data is not cleared after a week of
disuse).

**On Android** the flow is identical; the certificate in Step 8 is installed via
Settings > Security > Encryption & credentials > Install a certificate > CA certificate.

The iPhone flow above was exercised in Apple's iOS Simulator (iPhone 17) on 19 August 2026
through Step 6.

---

## 6. Accounts, and what each role can see


The file `mvp/.seed_credentials.txt` (Step 3) lists these accounts. It is git-ignored and
readable only by you.

| Username | Role | Sees |
|---|---|---|
| `dmc` | `super_admin` | Everything, all ULBs, user management, merges |
| `rwr_admin`, `bwl_admin` | `ulb_admin` | Their own ULB only |
| `field1` to `field6` | `field_officer` | Their own ULB; can look up **any** tag district-wide |
| `viewer` | `viewer` | Aggregate numbers only, no names, no photos |
| `auditor` | `auditor` | Read-only everything, including the audit log |

### Passwords: when they change, and how to make them stay

**Passwords are not regenerated when you start the system.** `make dev` and `make run` never
touch them; the seed refuses to run a second time and says so
(`users already exist — nothing to do`). The only thing that can change a password is one of
these three, and you have to ask for each of them:

| Command | Effect on passwords |
|---|---|
| `make dev`, `make run`, `make stop` | **None.** Start and stop as often as you like. |
| `make reseed` | **None.** Refreshes the demo animals and events; every account keeps the password it had, and nobody is signed out. |
| `make reseed-new-passwords` | **Reissues every one.** Only use this if you want a clean set. |
| `make set-password U=dmc` | Changes that one account to a password you type in. |

For the pilot, the sane setup is: **`make set-password U=<username>` once per real account.**
It prompts for the password twice, never shows it on screen, never puts it in a file, and it
survives everything above — including `make reseed`. Any existing sessions for that account
are signed out, which is the point of changing a password.

Two supporting details:

- If you would rather all the *demo* accounts share one password you choose, put it in
  `mvp/.env` as `SEED_PASSWORD=...` before the first seed. It applies to accounts as they are
  created and never changes afterwards.
- `.seed_credentials.txt` is rewritten by every seed, and it carries the passwords forward, so
  a `make reseed` does not lose your list. An account whose password you set by hand shows as
  `(set manually)` there — the file never prints a password that does not work, because ten
  wrong attempts locks the account for fifteen minutes.

**When this goes live, none of the above is how you make accounts.** Real officers get real
accounts on `/admin/users` as `dmc`: create the user, hand them the temporary password it
shows you once, and delete the demo `field1`–`field6` accounts and
`.seed_credentials.txt` entirely. The seeded accounts all have "(DEMO)" in their names so
they cannot be mistaken for real ones on a live register.

The demo runs on an **iPhone or an Android phone** in the same way (the app is a web app;
see the glossary under PWA). On Android, the certificate in Steps 13 to 14 is installed via
Settings > Security > Encryption & credentials > Install a certificate > CA certificate.
The iPhone flow above was exercised in Apple's iOS Simulator (iPhone 17) on 18 August 2026
for Steps 7 to 9.

---

## 7. What to click during a demo


A 5-minute run that shows the whole idea:

1. **`/cm` on the projector.** Headline numbers: animals on the register, % tagged, road
   sightings this week, animals removed from the road, fines collected. No personal data
   on screen.
2. **Click the "Tagged" figure on `/cm`,** then **"Where does this number come from?"** on
   the enlarged card. It turns over and shows what the percentage counts, the condition it
   was counted with, and where the records are. Do this once, early: it settles the "can we
   trust these numbers" question before it is asked, and it is the difference between a
   dashboard and a claim.
3. **`/admin`** as `dmc`. Point at the map of road sightings clustered on NH-48/NH-11, and
   at the "sightings by hour" chart — the two peaks are exactly the morning and evening
   grazing cycle the public is complaining about. That chart is the argument for when to
   deploy the catching teams. Click the chart to enlarge it; the room can read it from the back.
4. **Owners list: the "declared / on register" column.** The keeper's own stated herd size
   against what the team actually found. A red figure is a household with animals
   unaccounted for, and it is the list the next visit works from.
5. **Repeat offenders table.** This is the accountability story: the same handful of owners,
   with escalating fines, third offence flagged for FIR.
6. **Field app on the phone.** Register an owner — including the cattle count they declare
   and the shed size in square yards — → register an animal with a photo, an age in years, an
   identification mark or two, and a 12-digit tag → back on `/admin`, refresh, the animal is there.
7. **Look up a Bawal tag while logged in as a Rewari officer.** The animal is found —
   deliberately, because cattle wander across boundaries — but the owner's phone number is
   **masked** (`98XXXXXX78`) and the address is withheld. Note that the identification marks
   come back with it: that is what identifies an animal whose tag has been cut off.
8. **Try to open a Bawal owner's record directly** by pasting its id into the URL as a
   Rewari officer. You get **404 Not Found**, not "access denied".
9. **Audit → Verify chain.** Green "OK". Then show
   [section 6](#6-proving-the-data-has-not-been-tampered-with).

---

## 8. Where the data lives, and how to get it out


### Where it is

| What | Where exactly | Notes |
|---|---|---|
| The registry (owners, animals, events, fines, audit) | PostgreSQL database `gautrack`, files under `mvp/.pgdata/` | This folder **is** the registry. Copy it and you have copied everything. |
| Photographs | `mvp/data/photos/`, filed by content hash | The database stores the reference; the image bytes are on disk. |
| Passwords for the demo accounts | `mvp/.seed_credentials.txt` | Generated on this machine, git-ignored, readable only by you. |
| Application secrets (database password, cookie key) | `mvp/.env` | Generated on first run. Never committed. |
| Backups | wherever `make backup` writes (Section 10) | Encrypted. |

Nothing is in anyone's cloud. If the Mac is switched off, the registry is switched off.

### Getting it out: CSV downloads

Signed in as `dmc`, `rwr_admin` or `auditor`, these addresses download a spreadsheet
directly. They are ordinary links: paste into the browser, or click from the dashboard.

| Address | Gives you |
|---|---|
| `/api/export/owners.csv` | Every keeper in your scope, including the cattle count they declared and their premises area in square yards |
| `/api/export/animals.csv` | Every animal, with tag number, species, status, age in years and both identification marks |
| `/api/export/events.csv` | Every recorded action: registration, sighting, impound, fine |
| `/api/export/fines.csv` | Fines with amount, status and the legal instrument cited |
| `/api/export/shelters.csv` | Shelters with capacity and current count |

**Slicing it** — the two questions the office actually asks:

- One quarter's fines, for reconciling a contractor's bill:
  `…/api/export/fines.csv?from=2026-04-01&to=2026-07-31`
- One ULB only: `…/api/export/animals.csv?ulb=1`

Both filters can be combined. Dates are inclusive of the whole end day.

Three rules are enforced, and they matter if anyone asks how the data is protected:

1. **Not everyone may export.** Field officers cannot take a bulk copy of the keeper list,
   even though they may look up any tag on the road. The `viewer` role (intended for the CM
   office and press) cannot export personal records at all.
2. **Scope applies to files exactly as it does on screen.** A Bawal administrator exporting
   "all owners" gets Bawal's owners. The filter cannot be used to widen that.
3. **Every download is recorded** — who, which dataset, which filters, how many rows, from
   which address — in an append-only log that no one, including the person who exported,
   can delete afterwards. Bulk extraction of personal data is exactly the event an audit
   needs to see.

Two practical notes: the files open cleanly in Excel (UTF-8 with a byte-order mark, so
Hindi names and the rupee sign are not mangled), and any text that begins with `=`, `+`,
`-` or `@` is neutralised so a keeper named `=HYPERLINK(...)` cannot execute a formula on
the clerk's machine that opens the file.

### Getting it out: Power BI, Excel, or any BI tool

Your instinct here is right: for anything beyond the daily operational numbers, pointing a
real BI tool at the database is better than building more chart screens.

Create the read-only login once:

Terminal

```bash
cd ~/gautrack/mvp && make analyst-user
```

Expected output

```text
  Read-only analytics login ready.

    Host      127.0.0.1
    Port      55432
    Database  gautrack
    Username  gautrack_ro
    Password  tLX80rG9MK7yZTn7h1CELbuG
```

Then in **Power BI Desktop**: Get Data > PostgreSQL database > Server `127.0.0.1:55432`,
Database `gautrack`, and sign in with those details. In **Excel**: Data > Get Data > From
Database > From PostgreSQL Database.

This login can **only read**, and only the operational tables (`ulbs`, `owners`, `animals`,
`events`, `fines`, `shelters`, `fine_schedule`, and a pre-aggregated `v_daily_counts` view).
It cannot read the `users` table (password hashes) or `sessions` (live login tokens), and
it cannot write anything at all — the database itself refuses, not merely the application.
Re-running the command issues a new password and invalidates the old one.

**Where to draw the line between the two.** The built-in dashboard should stay the
operational screen: it is live, needs no licence, works on a phone, and is safe to show in
public because the `viewer` role carries no personal data. Power BI is the right tool for
analysis the office does for itself — cross-tabs, trends, a slide for a review meeting.
Building those as more dashboard pages would be re-implementing Power BI badly.

**One caution for later.** Power BI *Desktop* reading from this machine keeps the data in
your hands. Publishing to the **Power BI Service** uploads a copy to Microsoft's cloud,
which contradicts the data-residency position taken in the proposal and would have to be
settled with the IT department first. Desktop for now; the Service only after that
conversation.

---

## 9. Where the dashboard's numbers come from


Every number on `/admin` and `/cm` is calculated **live from the database at the moment you
load the page**. Nothing is pre-computed, cached overnight, or typed in by hand. Reload the
page and you are looking at the current state of the registry.

**You do not have to take that on trust, and neither does anyone you show it to.** Click any
figure or chart on either dashboard. The card lifts off the page, moves to the centre of the
screen and enlarges, so the room can read it from the back. That is the first click and it
is all most people want.

The second click is the one that settles arguments. **Where does this number come from?** on
the enlarged card turns it over. On the back is what the figure counts in plain English, the
table and the condition it was counted with, the scope that was applied, and a button that
opens exactly those rows in the registry. If someone asks "where does 74% come from", the
answer is two clicks away and ends on the actual records rather than on a slide.
**Back to the card** returns to the enlarged figure; **Close**, the Escape key, or a click
outside returns to the page.

The table below is the same information in one place, for reading ahead of a meeting.

| Dashboard element | Comes from | Counted as |
|---|---|---|
| Owners registered | `owners` table | Rows in your scope, duplicates merged out |
| Declared / on register (owners list) | `owners.self_declared_cattle_count` vs `animals` | What the keeper said, against what was found |
| Animals registered / Tagged % | `animals` table | Rows; tagged = has a tag number |
| Tagged today | `animals` | Created since midnight |
| Road sightings (7d) | `events` where type = `sighting_road` | Last 7 days |
| Impounded now / In gaushala | `animals.status` | Current status, not history |
| Fines issued / collected | `fines` table | Amounts and payment status |
| Repeat offenders | `fines` grouped by owner | Owners with 2 or more |
| Registrations per day chart | `owners` + `animals` + `events` | Grouped by day, 30 days |
| Sightings by hour chart | `events.occurred_at` | Grouped by hour, local time |
| Map of sightings | `events` latitude/longitude | Rounded to about 110 m |
| Shelter occupancy | `shelters.current_count` vs `capacity` | Updated by intake events |

Two deliberate choices worth knowing before anyone asks:

- **Public reports are excluded from the headline numbers.** Anyone can submit the "cow on
  the road" form without logging in. Those reports appear in the events feed for an officer
  to verify and act on, but they do **not** move the sighting counts. Otherwise anyone with
  a browser could manufacture a spike the day before a review.
- **Map points are rounded** to roughly 110 metres. An exact coordinate outside a house in
  a small village identifies a household; the cluster pattern on a road is what the
  dashboard actually needs.

The dashboard refreshes itself every 60 seconds. The `viewer` role sees the same numbers
with no names, no photographs and no per-owner pages, which is why that screen is safe to
project or hand to the press. On that screen the "open these records" button is not offered,
because that role genuinely cannot open them; it is given the aggregate figures instead.

**Reading the colours.** Colour on these screens means one thing only, and it is worth
saying out loud in a briefing:

- **Green, amber and red are percentages, and nothing else.** For tagging coverage, green is
  75% or better, amber is 40% or better, red is below 40%. For shelter occupancy the
  direction is reversed — red is 90% or fuller, because a full shelter is the point at which
  impounding stops being possible.
- **Every other colour is just a label.** Each chart has its own hue, repeated as a rule
  along the top of its card, so "the blue chart" and "the amber chart" are unambiguous
  references. No chart uses green to mean a category, so green never quietly changes meaning
  half way across the screen.

**Two screens, two shapes.** `/admin` puts nine figures on one line and four charts on the
next, sized to the window, and then scrolls for the map and the detail tables. `/cm` is a
single screen with no scrolling at all, built for a projector: banner, six figures, and one
band underneath that divides whatever height is left three ways.

---

## 10. Backups, and the restore drill


### Taking backups

```bash
make backup
```

Writes `backups/gautrack-<timestamp>.sql.gz` (the whole database) and
`backups/photos-<timestamp>.tar.gz`, records a checksum for each, and deletes copies older
than 14 days. `harden_vm.sh` schedules this at 02:30 nightly.

**Encrypt them.** A plain dump contains every cattle keeper's phone number and address.
Install [`age`](https://age-encryption.org) and set a recipient key:

```bash
age-keygen -o ~/.age/gautrack.key           # keep the private key OFF this server
export BACKUP_AGE_RECIPIENT=age1xxxxxxxx...  # the public key
make backup                                  # now produces .sql.gz.age
```

**Copy them off the machine.** A backup on the same disk as the database is not a backup:

```bash
rsync -az --delete /srv/backups/ backup@second-host:/srv/gautrack-backups/
```

### The restore drill — do this before you need it

Rehearse this **once now and once a quarter**. An untested backup is a guess.

```bash
scripts/restore.sh backups/gautrack-20260817-2100.sql.gz
# it asks you to type the database name to confirm
make verify-chain      # ALWAYS run this after a restore
```

The last step is the point: the hash chain tells you whether what came back is exactly
what went in.

---
## 11. Maintenance and repair


Things that go wrong, and what to do about them. Work down the list in order — the causes
are roughly in order of likelihood.

### A page will not open

1. **On the Mac, try `http://localhost:8000/admin`.** If that fails, the server is not
   running: go to Section 4 and start it, then look in that Terminal window for red error
   lines.
2. **Check what is actually running** with `make status` (Section 4). If the web server
   says `stopped`, that is your answer.
3. **On a phone, check the address.** `http://` and `:8000` for normal use; `https://` and
   `:8443` only after installing the certificate (Section 5).
4. **Same Wi-Fi?** The phone must be on the same network as the Mac, not mobile data. Hotel
   and guest networks often block phone-to-laptop traffic; a personal hotspot with the Mac
   joined to it works.
5. **Mac firewall.** System Settings > Network > Firewall (Section 5, Step 2). This is the
   most common cause of a phone that hangs forever.
6. **The Mac's address changed.** It changes on a different Wi-Fi. Run `make lan-ip` again.
7. **The Mac went to sleep.** The server pauses with it. Keep the lid open during a demo.

### "Session expired, please try again"

Not a fault. Admin logins expire after 2 hours idle, field logins after 12. Sign in again.

### "Address already in use", or changes are not appearing

Usually a server from an earlier session is still running and holding the port. `make stop`
now stops the web server as well as the database, which clears this. To confirm:

Terminal

```bash
cd ~/gautrack/mvp && make stop && make status
```

Expected output

```text
web server (:8000) : stopped
database          : stopped
```

### The database will not start

Look for a stale lock after an unclean shutdown (for example, the Mac lost power):

Terminal

```bash
cd ~/gautrack/mvp && cat .pgdata/server.log | tail -20
```

The log usually names the problem directly. A stale `postmaster.pid` after a crash is the
common one; deleting only that file and running `make dev` again is the fix. Do **not**
delete `.pgdata` itself — that is the registry.

### Photographs will not attach on a phone

Fixed on 19 August 2026. If an older copy of the app is cached on the phone, close all
Safari tabs for the site and reopen; the app carries a version marker that refreshes it.

### A dashboard change does not appear, even after reloading

The browser is showing a cached copy of the old `admin.css` or `admin.js`. Every page tags
those files with a version — `admin.css?v=68b2af14` — computed at start-up from the newest
modification time in `api/static/`. Changing that tag is the only instruction a browser
cache reliably obeys, so **restarting the web server is what publishes a front-end change**,
not saving the file. `make dev` reloads automatically when a Python file changes, but the
version stamp is read once at start-up, so after editing CSS or JavaScript:

```bash
make stop
make run
```

Then reload the page normally. If it is still stale, the page was opened before the restart
and is holding the old tag: reload once more.

### After changing anything in `api/`

The development server reloads by itself. If it does not, stop and start it (Section 4).

### Checking the software is still sound

Terminal

```bash
cd ~/gautrack/mvp && make test && make verify-chain
```

88 tests should pass and the audit chain should report OK. Run this after any change, and
before any demonstration that matters.

---
## 12. Resetting: what it destroys, and why you would almost never do it


There are two destructive commands. Neither is part of normal operation.

| Command | What it destroys | When it is appropriate |
|---|---|---|
| `make reseed` | All records; reloads the demo dataset; **regenerates every password** in `.seed_credentials.txt` | Demo machine only, when you want a clean demo again |
| `make db-reset` | The entire database including its structure | Development only, when the database is broken beyond repair |

### Why you would essentially never reset a live system

You are right to be suspicious of the idea. On a live registry, resetting would destroy the
only record of which animal belongs to whom, every fine issued, every shelter intake, and
the audit trail that proves none of it was altered. There is no operational problem that
"start again from empty" solves:

- **Wrong data in a record?** Correct it. Corrections are new entries; the original stays
  visible in the history, which is what makes the registry defensible if a fine is
  challenged.
- **Duplicate owner created by two officers?** Merge them from the admin screen. The merge
  is recorded.
- **Software broken after an update?** Restore the previous version of the software. The
  data is separate and does not need touching.
- **Database corrupted?** Restore from backup (Section 10). That is what backups are for,
  and it keeps the history.

Deleting a government registry to fix an operational problem would also be the single most
suspicious action available to anyone with access. The system is deliberately built so
that even the administrator cannot quietly erase history: events and audit records are
append-only *at the database level*, so the application literally lacks permission to
delete them.

**The only legitimate uses of a reset are:** this demo machine before a fresh
demonstration, and a development machine. On the production server, the equivalent
operations are "restore from backup" and "retire and archive", never "reset".

If you ever run `make reseed` on the demo machine, note that all the passwords change —
re-read `.seed_credentials.txt` afterwards.

---
## 13. Running the tests


```bash
make test
```

This creates a **separate throwaway database** called `gautrack_test`, builds the schema
into it, runs every test, and leaves your demo data alone.

Current result: **79 passed**. What they prove:

| File | What it proves |
|---|---|
| `test_auth.py` | Every non-public address returns **401** without a login. Ten wrong passwords lock the account for 15 minutes. A cross-site request without the right headers is refused. |
| `test_authz.py` | A Rewari officer cannot read *or* edit a Bawal owner or animal (**404**, never 403). The same officer **can** look up a Bawal tag, with the phone masked. `viewer` cannot reach any personal record but can read the statistics. Only `super_admin` manages users and merges owners. |
| `test_sync.py` | Uploading the same batch three times creates the data once and reports `duplicate` after that. Two officers registering the same tag → the second gets `conflict` plus a summary of the existing animal. One bad row in a batch does not discard the good ones. The database physically refuses to edit or delete an event. |
| `test_photos.py` | A photo whose checksum does not match is rejected (**400**). A shell script or HTML file renamed to `.jpg` is rejected — we read the file's *magic bytes*, not its name. A photo belonging to another ULB cannot be fetched. |
| `test_audit.py` | Every change is logged against the right user. Password hashes never enter the log. The seal verifies OK; editing a row directly in SQL makes it report **BROKEN**; putting it back makes it **OK** again. |
| `test_export.py` | Only super_admin, ulb_admin and auditor may download a CSV; a field officer and the `viewer` role cannot. A ULB admin's file contains only their own ULB, and a filter cannot widen it. A name beginning `=` is neutralised so it cannot execute in Excel. Every download is recorded, and the record cannot be deleted. |

---

## 14. Proving the data has not been tampered with


This is the part that matters politically. If an owner is fined and claims the record was
fabricated — or if someone inside the office is accused of deleting a record — you need to
be able to answer with evidence rather than assurance.

### How it works

Every change to an owner, animal, user, fine or shelter writes a row into the `audit_log`
table, automatically, inside the database itself — the application cannot skip it, and the
application's database account has no permission to edit or delete those rows.

Each row carries a **hash chain**:

```
this row's seal = SHA-256( previous row's seal + the contents of this row )
```

A *hash* is a fingerprint: change one character anywhere and the fingerprint changes
completely, and you cannot work backwards from a fingerprint to the contents. Because each
row's seal includes the previous row's seal, the rows are linked like a chain. Alter any
row and every seal after it stops matching.

### Checking it

```bash
make verify-chain
```

```
audit_log  OK      rows=373     tip=4320ab45af3a44a6…
events     OK      rows=455     tip=035a0afd8e69aec9…

RESULT: OK — no row has been altered.
```

There is also a **Verify chain** button on the dashboard's Audit page.

### Seeing it catch a forgery

This was run against the live demo database. First, note that even a database
administrator cannot simply edit history:

```
$ UPDATE events SET payload='{"forged":true}' WHERE seq=1;
ERROR:  events is append-only; UPDATE is not permitted
```

To tamper at all you must first deliberately switch off that guard. Doing so, and
rewriting one owner's name inside the audit log:

```
audit_log  BROKEN  rows=373     tip=4320ab45af3a44a6…
           first broken id = 16
           content_broken=True link_broken=False
           table=owners action=INSERT row=01a010ba-5958-7a9a-910b-bb3fd29a5f61

RESULT: BROKEN — the database has been tampered with.
```

It names the exact row. Restoring the original value returns it to `OK`.

### The daily anchor — and the one thing the chain cannot do on its own

A chain detects **edits** and **deletions from the middle**. Cutting rows off the **end**
leaves a shorter chain that is still internally consistent — no self-check can see that.

The fix is to publish the chain's current tip once a day:

```bash
make anchor
```

```
GauTrack daily anchor — Rewari district
date               : 2026-08-17
chain status       : OK
AUDIT TIP HASH     : 4320ab45af3a44a6...
EVENT TIP HASH     : 035a0afd8e69aec9...
```

E-mail those two lines to the DMC and the auditor, or put them in the day's file noting.
Once a tip hash is on the record outside the database, nothing logged before it can be
removed without that published hash vanishing from the chain. `harden_vm.sh` schedules
this automatically at 03:15 every day.

---

## 15. Showing it live over the internet, before there is a server


The laptop demo (sections 4 and 5) proves the **software** works. It does not prove the
**system** works, because the system is four different people on four different devices at
the same time: a field officer on a phone out in a ward, the DMC on an office computer, the
CM on his own computer somewhere else, and a member of the public on their own phone. To
show that, the software has to sit somewhere all four can reach, which means somewhere on
the internet.

This section is how to do that **before the department has bought anything**. It is a demo
path, not a deployment path. Section 16 is the deployment path.

### First, what will not work, and why

It is worth knowing this because it is the first thing anybody suggests.

| Suggestion | Why it cannot host GauTrack |
|---|---|
| **GitHub Pages** | Serves *static* files only: HTML, images, CSS. It cannot run a program. GauTrack is a Python web server talking to a PostgreSQL database, and neither can exist there. |
| **Vercel / Netlify** | Built for websites whose pages are generated in advance. They do not run a long-lived server process or a database of this kind, and the offline-sync and photo storage have nowhere to live. |
| **Emailing a link to `localhost:8000`** | `localhost` means "the machine I am sitting at". On somebody else's computer that address points at *their* machine, where nothing is running. |

**GitHub itself is where the code is *stored*, not where it *runs*.** That distinction is
the whole of this section. What runs the code is a **GitHub Codespace**: a Linux computer
that GitHub starts for you, from your repository, on their machines, with a terminal and an
editor in the browser. It is an ordinary virtual machine that happens to be created from a
repository, and it is the closest thing to the real server you can have without buying one.

### Step 1 — Put the code in a private repository

**Git** is version control: it records the state of a folder over time, so any change can be
inspected or undone. A **repository** ("repo") is that folder plus its history. **GitHub** is
a company that stores repositories online.

```bash
cd ~/gautrack          # the folder that contains mvp/
git init
git add -A
git status          # READ THIS. It lists exactly what is about to be stored.
```

**Before committing, check `git status` does not list any of these:**

- `mvp/.env` — the secret keys and database passwords
- `mvp/.seed_credentials.txt` — the demo account passwords
- `mvp/data/` — the photographs
- `mvp/.pgdata/` — the database files themselves

They are excluded by `.gitignore` at the repository root and by `mvp/.gitignore`, but check
rather than trust: a secret that reaches a repository has to be treated as compromised even
after it is deleted, because the history keeps it.

```bash
git commit -m "GauTrack: registry, dashboards, research and proposal"
```

Then, on github.com, create a **private** repository and push to it:

```bash
git remote add origin https://github.com/<your-account>/<repo-name>.git
git branch -M main
git push -u origin main
```

> **Private, not public, and this is not a formality.** The repository carries the proposal,
> the research, the threat model and the deployment instructions for a government system. A
> public repository is readable by anyone in the world, including whoever would rather the
> stray-cattle plan did not work. Make it private at creation time; changing it afterwards
> does not un-publish what was already read.

> **A personal GitHub account is acceptable *for a demo only*.** The rule that nothing lives
> on a personal account applies to the real registry, which holds real people's names and
> phone numbers. A demo with 240 invented cows does not. When the department goes live, the
> code moves to a department-owned account or is simply copied to the server directly;
> nothing in the software depends on GitHub existing.

### Step 2 — Start a Codespace

On the repository page: **Code → Codespaces → Create codespace on main**.

GitHub reads [`.devcontainer/devcontainer.json`](../.devcontainer/devcontainer.json) — a
recipe file that says which Linux image to start, that Docker and Python 3.12 are needed,
and that port 8000 should be shared. It then builds the machine and installs the Python
dependencies. The first build takes a few minutes; later ones are quick because GitHub
caches it.

### Step 3 — Run it

In the Codespace terminal:

```bash
make -C mvp dev
```

Exactly the same command as on the laptop, doing exactly the same thing: start PostgreSQL,
apply the migrations, load the demo data, run the web server on port 8000.

### Step 4 — Make the address public, and copy it

Open the **Ports** tab at the bottom of the Codespace window. Port 8000 will be listed with
an address of the form:

```
https://<something>-8000.app.github.dev
```

Right-click the row → **Port Visibility → Public**.

**Do not skip this.** By default a forwarded port is private, which means GitHub asks anyone
who opens the address to sign in to *your* GitHub account first. A field officer's phone and
a member of the public cannot do that, so to them the demo simply looks broken. Public
visibility removes that check; the address itself stays unguessable.

That address is now the whole system, and every surface hangs off it:

| Who | Address to give them |
|---|---|
| Field officers | `https://<...>.app.github.dev/app` |
| DMC and office | `https://<...>.app.github.dev/admin` |
| CM / viewer screen | `https://<...>.app.github.dev/cm` |
| General public | `https://<...>.app.github.dev/report` |

It is served over **HTTPS**, which matters for more than appearances: Android refuses to
give a web page the camera or the GPS unless the connection is secure, so the field app can
only photograph and geo-locate an animal on an `https://` address. This is the same reason
`make dev-tls` exists for the Wi-Fi demo, and the Codespace gets it for free.

### Step 5 — What to actually demonstrate

The point of this path is the things a laptop cannot show. Run them in this order:

1. **Open `/app` on a phone**, on mobile data, not office Wi-Fi. Sign in as `field1`.
   Register an owner and an animal with a photograph. This proves the phone is talking to a
   machine somewhere else, not to something on the same desk.
2. **On the office computer, open `/admin`** and refresh. The animal that was just recorded
   on the phone is there, with the officer's name against it. That is the whole product in
   one moment.
3. **Turn the phone's data off**, record another sighting, turn it back on. It syncs. Field
   staff work in places with no signal, and this is the answer to "what happens then".
4. **Open `/report` on a completely different phone**, one with nobody signed in, and file a
   public report with a photograph. Then show it arriving in `/admin` — and show that it is
   **not** counted in the headline figures until an officer verifies it.
5. **Sign in to `/cm` as `viewer`** on a third device. Show that the record pages are not
   merely hidden but genuinely unreachable for that account.

### Step 6 — Stop it when you are done

```bash
make -C mvp stop
```

Then, on github.com, **Codespaces → … → Stop codespace**. A Codespace that is left running
consumes the account's free monthly allowance and, past it, costs money. A stopped Codespace
keeps its files and restarts in seconds.

### The limits of this path, stated plainly

- **The data sits on GitHub's machines**, which are outside India. That is fine for invented
  cows and unacceptable for real keepers' names and phone numbers. This is a demo, and the
  banner across the top of every page says so.
- **The address changes** each time the Codespace is rebuilt. It is not something to print
  on a poster.
- **It is not always on.** A stopped Codespace serves nothing, and it stops itself after
  about half an hour of inactivity.
- **The free allowance is finite.** Roughly 60 machine-hours a month on a personal account,
  at the time of writing. Check your own account's billing page rather than trusting that
  number.

Everything above is why section 16 exists. When the department is ready for real data, the
same repository is copied to a machine the department controls, `SEED_DEMO` is set to `0`,
and the demo path is simply abandoned. Nothing has to be rebuilt.

> **Not yet executed.** The `devcontainer.json` recipe is written but has not been run,
> because Docker is not installed on the machine this was built on. The first Codespace
> build is its first real test. If it fails, the fallback needs no recipe at all: create the
> Codespace anyway, then run `make -C mvp dev` — the same commands work on any Ubuntu
> machine with Python 3.12 and Docker.

---

## 16. Deploying to a real server


**Target:** an Ubuntu 24.04 virtual machine in an **India region** (Mumbai or Delhi — keep
citizen data inside the country), **2–4 vCPU, 8 GB RAM, 100 GB disk**. That comfortably
carries the whole district.

### Step 1 — DNS

Point a name such as `cattle.rewari.gov.in` at the machine's IP address. Caddy needs a
real name to obtain a certificate; it cannot certify a bare IP.

### Step 2 — Harden the machine, before anything else

```bash
scp scripts/harden_vm.sh root@<server-ip>:/root/
ssh root@<server-ip>
bash harden_vm.sh --admin-user gautrack --ssh-key "ssh-ed25519 AAAA... you@laptop"
```

This sets up: a firewall allowing only ports 22, 80 and 443; SSH by key only, no
passwords, no root login; `fail2ban` to ban repeated failed logins; automatic security
updates; Docker; log rotation; and the nightly backup and anchor jobs.

> **Before you log out, open a second terminal and confirm you can still SSH in.**
> Locking yourself out of a hardened machine is the classic first mistake.

### Step 3 — Copy the code and write the configuration

```bash
sudo mkdir -p /srv/gautrack && sudo chown gautrack:gautrack /srv/gautrack
# from your laptop:
rsync -az --exclude .venv --exclude .pgdata --exclude .env \
      ~/gautrack/mvp gautrack@<server-ip>:/srv/gautrack/

ssh gautrack@<server-ip>
cd /srv/gautrack/mvp
cp .env.example .env
nano .env
```

Set at minimum:

```ini
SECRET_KEY=<output of: openssl rand -hex 32>
POSTGRES_PASSWORD=<output of: openssl rand -hex 24>
APP_DB_PASSWORD=<output of: openssl rand -hex 24>
SITE_ADDRESS=cattle.rewari.gov.in
ACME_EMAIL=it-cell@rewari.gov.in
SEED_DEMO=0          # <-- 0 for real use, or you will ship 240 fake cows
COOKIE_SECURE=1
```

```bash
chmod 600 .env
```

### Step 4 — Start

```bash
docker compose up -d --build
docker compose logs -f api      # watch it migrate and start
```

Caddy obtains the TLS certificate on the first request and renews it forever. Open
`https://cattle.rewari.gov.in/admin`.

### Step 5 — Create the first real user

With `SEED_DEMO=0` there are no accounts, so make one from the command line:

```bash
docker compose exec api python -c "
import auth, ids
from db import SessionLocal
from models import User, Role
import secrets
pw = secrets.token_urlsafe(12)
with SessionLocal() as db:
    db.add(User(id=ids.uuid7(), username='dmc', password_hash=auth.hash_password(pw),
                full_name='District Municipal Commissioner', role=Role.super_admin))
    db.commit()
print('username: dmc')
print('password:', pw)
"
```

Write the password down, sign in, and create everyone else from **Users** on the dashboard
— that path records who created whom in the audit log.

> **Turn on two-factor authentication for every `super_admin` and `ulb_admin` account.**
> Tick "Two-factor: on" when creating the user; the dashboard then shows an `otpauth://`
> link to paste into Google Authenticator.

---

## 17. Hardening checklist


Before this holds real citizen data, tick every line.

**Machine**
- [ ] `harden_vm.sh` has been run; firewall allows only 22, 80, 443
- [ ] SSH is key-only; root login disabled; you have tested a second login
- [ ] `fail2ban` and unattended security upgrades are active
- [ ] Only named officials have shell access, each with their own key

**Application**
- [ ] `SEED_DEMO=0` and the demo data is gone
- [ ] `.env` is mode `600` and has never been committed to version control
- [ ] `SECRET_KEY`, `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` are freshly generated, ≥32 chars
- [ ] `mvp/.seed_credentials.txt` has been deleted
- [ ] Every admin account has two-factor authentication switched on
- [ ] Every user has their own account — **no shared logins**, or the audit log is worthless
- [ ] Staff who have left are disabled (Users → Disable), not just forgotten

**Data**
- [ ] Nightly backups run, are encrypted, and are copied to a second machine
- [ ] A restore has actually been rehearsed end to end
- [ ] `make anchor` runs daily and the tip hashes go to the DMC and the auditor
- [ ] `make verify-chain` is run (and its output filed) before any figure is quoted publicly

**Legal and policy**
- [ ] The fine schedule in `fine_schedule` has been confirmed against the Haryana Gauvansh
      Sanrakshan & Gausamvardhan Act 2015 and ULB rules — **the seeded amounts are a
      placeholder**
- [ ] A data-retention rule is agreed (how long sightings and photographs are kept)
- [ ] The public `/report` page's wording has been cleared
- [ ] Someone is named as the data controller

---

## 18. Threat model, in plain terms


Who might attack this, what they would try, and what stops them.

| Who | What they try | What stops them |
|---|---|---|
| **An owner who does not want to be fined** | Cuts the ear tag off | Every animal also has a photograph, an optional muzzle close-up (a muzzle print is as individual as a fingerprint), colour and markings, and a GPS-located owner. A `tag_lost` event flags the animal, and repeated tag loss on the same owner is itself a pattern. |
| | Registers the same tag on a different animal | Tag numbers are unique district-wide. The second attempt is refused with `conflict` and shows the officer what the tag is already on. |
| | Denies the record exists | The hash chain plus the daily anchor. |
| **A curious or corrupt insider** | Opens records from another ULB by pasting an id | Every query is filtered by the signed-in user's jurisdiction. The record returns **404**. Proven by the tests. |
| | Edits a record to help a relative | The edit is written to the audit log with their user id, IP and the before/after values. `verify-chain` catches any later attempt to remove that entry. |
| | Deletes an inconvenient event | The database refuses: `events` is append-only, and the application's database account has no `DELETE` permission at all. |
| **Someone who steals a database dump** | Uses the session tokens in it | Only hashes of session tokens are stored — the dump yields no usable logins. |
| | Cracks the passwords | Passwords are stored with **argon2id**, the current recommended algorithm, deliberately slow and memory-hungry. |
| **An attacker on the internet** | Guesses passwords | 5 attempts per minute per IP address, 10 per hour per account, then a 15-minute lockout. Every attempt is recorded. |
| | Tricks a logged-in officer into clicking a malicious link that performs an action | Session cookies are `SameSite=Lax` and every state-changing request needs both a custom header and a per-session token that a foreign site cannot read. |
| | Injects a script into a name field to steal a session | Session cookies are `httpOnly` (unreadable by scripts), output is escaped, and the Content-Security-Policy allows only this site's own scripts, each carrying a per-request nonce. |
| | Uploads a web shell disguised as a cow photo | Uploads are checked by *magic bytes*, not filename; only JPEG and PNG are accepted; files are stored outside the web root under a server-chosen name and served only through an authenticated route. |
| | Floods the public form with fake reports | 10 per hour per IP, a hidden honeypot field, and public reports are stored with `user_id = NULL` and `source = public`, so they can never be mistaken for an officer's observation. |
| **A rival vendor or an RTI request** | "Where does the data live and who else can see it?" | One machine, in India, that the district controls. No third-party service is contacted at runtime. The only outbound request a browser makes is for map tiles, and that address is a single configuration value you can point at a self-hosted map. |

### What this does *not* protect against

- **A `super_admin` who decides to be dishonest.** They can change records. What they
  cannot do is change them *invisibly*: every action is attributed and sealed. Keep the
  number of `super_admin` accounts very small, and give the auditor account to someone
  outside the DMC office.
- **Someone with the server's root password.** They can switch off the guards — but the
  daily published anchor still exposes it. This is why the anchor must leave the building.
- **Wrong data entered honestly.** A wrong tag typed at the roadside is a wrong tag in the
  registry. Photographs and GPS are the check on this.

---

## 19. Moving to HARTRON / State Data Centre / NIC


### What these three actually are

Government departments in India are not supposed to rent computers from whoever they like,
and there are three bodies whose job is to provide them instead. They are institutions, not
products, and the difference between them is who owns them.

- **HARTRON** — the *Haryana State Electronics Development Corporation*, a Haryana
  Government undertaking. It is the state's own IT arm: it supplies hardware, hosting,
  software and trained manpower to Haryana departments, and it is the body a Rewari
  department would normally approach first, because it belongs to the same government.
- **The State Data Centre (SDC)** — the physical facility the state runs to host its
  departments' systems, built under the national e-governance programme. Where HARTRON is
  the organisation, the SDC is the building with the machines in it. In practice a request
  to HARTRON often results in a virtual machine at the SDC.
- **NIC** — the *National Informatics Centre*, the Government of India's own IT
  organisation, present in every district. NIC runs the national cloud (MeghRaj), issues
  `gov.in` domain names, and hosts a great deal of district-level software. It answers to
  the centre rather than the state.

**Why this matters to the pitch, in one sentence:** the CM's officials will not ask whether
the software is good, they will ask *where it will live*, and "HARTRON or NIC, once it has
passed a CERT-In audit" is the answer that ends that question. Any of the three is
acceptable; which one is a decision for the department's IT cell, not for this document.

**What it costs you in time.** These are applications with approvals attached, not a signup
form. Weeks, sometimes months. That is precisely why sections 15 and 16 exist: the demo and
the pilot do not wait for it, and nothing has to be rebuilt when it arrives.

### The migration itself

Deliberately dull, which is the point. There is nothing proprietary to unpick.

1. Provision a VM there and run `harden_vm.sh`.
2. `rsync` this folder across.
3. `make backup` on the old machine; copy the dump and the photo tarball.
4. `scripts/restore.sh <dump>` on the new one.
5. `make verify-chain` — **if this says OK, the migration is provably complete and
   unaltered.** This is the strongest thing you can put in a handover note.
6. Repoint DNS. Caddy obtains a fresh certificate automatically.

If their policy is "no Docker", the pieces are ordinary too: PostgreSQL 16, a Python 3.12
process behind any reverse proxy (nginx, Apache). `api/Dockerfile` and `api/entrypoint.sh`
document exactly what to run.

---

## 20. CERT-In audit checklist (stub)


*A starting point for the empanelled auditor, not a substitute for the audit.*

| Area | Where to look | Status |
|---|---|---|
| Authentication | argon2id; server-side sessions; TOTP available | Implemented |
| Session management | 256-bit tokens, only hashes stored; 12 h field / 2 h admin idle expiry; revocation on password reset and account disable | Implemented |
| Access control | Per-request scope object; every object query ULB-filtered; tested | Implemented, `tests/test_authz.py` |
| Input validation | Pydantic on every request; magic-byte checks on uploads | Implemented |
| SQL injection | Parameterised queries throughout; no string-built SQL with user input | Implemented |
| XSS | Jinja auto-escaping; CSP with per-request nonce; `httpOnly` cookies | Implemented |
| CSRF | SameSite=Lax + custom header + per-session token | Implemented |
| Cryptography | TLS 1.2+ via Caddy; SHA-256 chains; argon2id | Implemented |
| Audit trail | Database triggers, hash-chained, app cannot write to it | Implemented |
| Logging & monitoring | Caddy access logs, rotated; login attempts table | Partial — no SIEM |
| Data minimisation | No Aadhaar numbers; phone masked outside ULB | Implemented |
| Backup & recovery | Nightly encrypted dump; documented restore drill | Implemented, **rehearse it** |
| Patch management | `unattended-upgrades`; pinned Python dependencies | Implemented |
| Penetration test | — | **Not done** |
| Data retention policy | — | **Not defined — DMC decision** |
| Privacy notice | — | **Not drafted** |
| Incident response plan | — | **Not drafted** |

---

## 21. Repository layout


```
.devcontainer/
  devcontainer.json       recipe for a GitHub Codespace (section 15)
mvp/
  README.md               this file
  ACCESS_SHEET.html       one printable page: who needs what to use the system
  DEVIATIONS.md           every difference from SPEC.md, with reasons
  SPEC.md                 the build specification
  Makefile                every command you need
  docker-compose.yml      production: db + api + caddy
  docker-compose.dev.yml  development: database only
  Caddyfile               production reverse proxy + TLS
  Caddyfile.dev           local HTTPS for the phone demo
  .env.example            configuration template (copy to .env)
  db/init/                creates the low-privilege database account
  api/
    main.py               app assembly, security headers, CSP nonce
    config.py             all settings, read from .env
    db.py                 two database connections: app role and owner role
    models.py             the tables
    schemas.py            what a valid request looks like
    auth.py               passwords, sessions, TOTP, rate limits, CSRF
    authz.py              who may see what  <- the IDOR defence lives here
    sync.py               registry writes, conflict rules, offline batch
    stats.py              dashboard aggregates
    audit.py              hash-chain verification
    photos.py             upload checks and photo access control
    seed.py               the demo dataset
    routes/               one file per group of addresses
    alembic/              migrations (the scripts that build the tables)
    static/vendor/        Chart.js, Leaflet, HTMX + CHECKSUMS.txt
    static/app/           the field app (PWA)
    templates/            the dashboard, CM view and public form
    static/admin.css      one stylesheet for every dashboard screen
    static/insight.js     the click-to-enlarge, turn-over card
    static/dashboard.js   the admin overview's charts and map
    static/cm.js          the CM screen's chart
  scripts/
    dev_db.sh             starts the development database
    dev_tls.sh            local HTTPS for the phone demo
    lan_ip.sh             prints the phone URL
    vendor_libs.sh        re-downloads the pinned front-end libraries
    verify_chain.py       tamper check
    anchor.py             the daily published tip hash
    backup.sh restore.sh  backups and restores
    harden_vm.sh          server hardening
  tests/                  the test suite
```

### About `static/vendor/`

The dashboard needs three JavaScript libraries: **Chart.js** (charts), **Leaflet** (map)
and **HTMX** (live-refreshing fragments). Most sites load these from a *CDN* — someone
else's server on the public internet. This one does not: they were downloaded once, at
pinned versions, and their SHA-256 checksums recorded in
`api/static/vendor/CHECKSUMS.txt`.

Why it matters: a CDN is a third party who can change the code running inside your
dashboard, and it tells that third party every time an official opens the page. Verify at
any time with:

```bash
scripts/vendor_libs.sh verify
```

---

## 22. Every `make` command


| Command | What it does |
|---|---|
| `make dev` | Everything: database, migrations, demo data, web server on port 8000 |
| `make run` | Web server only, without reloading the demo data |
| `make dev-tls` | HTTPS front end on port 8443 for the phone demo (second terminal) |
| `make lan-ip` | Prints the address to open on the phone |
| `make test` | Runs the test suite against a throwaway database |
| `make seed` | Reloads the demo data |
| `make migrate` | Applies database migrations |
| `make verify-chain` | Checks the tamper-evident seals |
| `make anchor` | Prints today's tip hash to publish |
| `make backup` | Database dump + photo archive into `backups/` |
| `make restore F=<file>` | Restores from a backup |
| `make vendor` | Re-downloads the pinned front-end libraries and rewrites checksums |
| `make db-psql` | Opens a database shell |
| `make db-reset` | **Deletes** the development database |
| `make stop` | Stops **everything**: web server and database |
| `make status` | Shows what is currently running |
| `make api-stop` | Stops just the web server |
| `make analyst-user` | Creates/resets the read-only login for Power BI or Excel |
| `make runbook` | Rebuilds both PDFs: this runbook and the technical architecture document |
| `make reseed` | Wipes records and reloads demo data (regenerates all passwords) |
| `make audit` | Checks the pinned dependencies against the public vulnerability database and lints the Python for security mistakes |
| `make help` | Lists all of the above |

### Commands that are not `make`

Everything above runs the software. These put it somewhere other people can reach, and are
the ones introduced in sections 15 and 16. They are ordinary tools, not project-specific.

| Command | What it does |
|---|---|
| `git init` | Starts recording the history of this folder |
| `git status` | Lists what is about to be stored. **Read it before every commit** |
| `git add -A` | Stages every change for the next commit |
| `git commit -m "…"` | Records the staged changes, with a message saying why |
| `git remote add origin <url>` | Names the online copy this folder pushes to |
| `git push -u origin main` | Uploads the history to that online copy |
| `git pull` | Brings down changes made elsewhere, e.g. edits made inside a Codespace |
| `make -C mvp dev` | The usual `make dev`, run from the repository root instead of from `mvp/` |
| `ssh <user>@<server>` | Opens a command line on the server |
| `scp <file> <user>@<server>:<path>` | Copies one file to the server |
| `rsync -az <folder> <user>@<server>:<path>` | Copies a folder, sending only what changed |
| `docker compose up -d --build` | Builds and starts the production stack on the server |
| `docker compose logs -f api` | Watches the web server's output live. Ctrl-C stops watching, not the server |
| `docker compose ps` | Shows which parts of the production stack are running |
| `docker compose down` | Stops the production stack. Data on the volumes survives |
| `openssl rand -hex 32` | Generates a secret key or a database password |

> **`docker compose down` versus `docker compose down -v`.** The first stops the containers
> and keeps the data. The second adds `-v`, which deletes the volumes — the database and
> every photograph — permanently and without asking. Never type the second on a live
> machine.

---

## 23. Known gaps


Honest list. See `DEVIATIONS.md` for the full detail.

1. **The Docker stack has been written but not executed** — Docker is not installed on the
   build machine. `docker compose up -d --build` on the VM is the first real test of it.
   The same applies to `.devcontainer/devcontainer.json`: the first Codespace build is its
   first real test, and section 15 gives the fallback if it fails.
   Everything else, including all 88 tests, ran on real PostgreSQL 16.
2. **No "change my own password" screen.** An admin can reset a password (and it revokes
   that user's sessions), but a user cannot rotate their own temporary password. Add this
   before real use.
3. **Photos are downscaled on the phone, not re-encoded on the server.** The server checks
   size, type and checksum. A hostile client could still upload a 5 MB valid JPEG. This is
   a disk-space concern, not a security hole.
4. **The fine schedule is a placeholder.** Confirm the legal amounts before quoting them.
5. **No pagination** on the dashboard lists — they are capped at 200 rows.
6. **Two-factor authentication shows a text URI, not a QR code.**
7. **The PWA icons are generated placeholders.**
8. **`/cm` requires a login.** Making it genuinely public is a one-line change once the DMC
   decides the numbers are public.

---

## Glossary


Terms in the order you are likely to meet them.

**VM (virtual machine)** — a rented computer in a data centre. Behaves exactly like a
physical server; you can move it between providers.

**Docker / container** — a way of packaging an application with everything it needs so it
runs identically on any machine. **Docker Compose** starts several containers together —
here: database, application, reverse proxy.

**PostgreSQL ("Postgres")** — the database. Free, open source, used by governments
worldwide. Nobody is billed per record and nobody can withdraw it.

**Reverse proxy** — the program facing the internet. It handles encryption and passes
requests inward. Ours is **Caddy**, chosen because it obtains and renews certificates by
itself.

**TLS / HTTPS / certificate** — the padlock in the browser. Encrypts traffic so it cannot
be read or altered in transit. A **certificate** proves you are talking to the right
server. A **certificate authority (CA)** is who vouches for it — publicly trusted ones for
real sites, a local one for the laptop demo.

**Secure context** — a browser's term for a page loaded over `https` (or from
`localhost`). Browsers refuse GPS, camera and offline caching outside one. This is why the
phone demo needs `make dev-tls`.

**Migration** — a versioned script that changes the database structure. Run in order, they
build the schema from nothing; the tool here is **Alembic**. It means the database on the
laptop, the demo server and the state data centre are provably identical.

**Schema** — the shape of the database: which tables exist, which columns, which rules.

**Seed data** — realistic fake data for demonstrations. Switched off with `SEED_DEMO=0`.

**PWA (Progressive Web App)** — a website that behaves like an installed app: it goes on
the home screen, opens without an address bar, and works offline. Nothing to publish to
the Play Store, nothing to approve, updates are instant.

**Service worker** — the small program the browser keeps running in the background that
makes offline work possible. It holds the app's own files so the app opens with no signal.

**IndexedDB** — the phone's own storage, where entries wait until they upload. Survives
closing the app and restarting the phone.

**Sync / idempotent** — uploading queued entries. *Idempotent* means sending the same
batch twice has the same effect as sending it once — essential when a phone loses signal
mid-upload and cannot tell whether the server received it.

**API** — the program the app and dashboard talk to. The only thing allowed to touch the
database.

**Endpoint / route** — one address the API answers on, e.g. `/api/owners`.

**Session / cookie** — how the server remembers you are signed in. **`httpOnly`** means a
malicious script cannot read the cookie. **`SameSite=Lax`** means another website cannot
make your browser send it.

**argon2id** — the algorithm that stores passwords. Deliberately slow and memory-hungry,
so guessing them wholesale is impractical even if the database is stolen.

**TOTP / two-factor authentication** — the six-digit code from an authenticator app. A
stolen password alone is then not enough.

**Hash / SHA-256** — a fingerprint of some data. Change one character and the fingerprint
changes completely; you cannot work backwards from it.

**Hash chain** — each record's fingerprint includes the previous record's fingerprint, so
records are linked. Alter one and every fingerprint after it stops matching. This is what
makes the audit log tamper-*evident*.

**Anchor** — publishing the chain's current fingerprint outside the database (e-mail, file
noting, printout) so that even deleting the end of the chain becomes detectable.

**Append-only** — rows may be added but never changed or removed. Enforced here by the
database itself, not merely by the application.

**Audit log** — the automatic record of who changed what, when, from which address, with
the before and after values.

**IDOR (Insecure Direct Object Reference)** — the classic mistake where changing a number
or id in the address bar shows you someone else's record. Prevented here by filtering
every query by the signed-in user's jurisdiction, so an out-of-scope record simply does
not exist as far as the query is concerned.

**Authentication vs authorisation** — *authentication* is "who are you" (signing in);
*authorisation* is "what are you allowed to see" (a Bawal officer cannot open Rewari
records).

**Scope / role** — the jurisdiction and permissions attached to your account:
`super_admin`, `ulb_admin`, `field_officer`, `viewer`, `auditor`.

**CSRF (Cross-Site Request Forgery)** — tricking your browser, while you are signed in,
into performing an action on another site's behalf. Blocked by the cookie rules plus a
token only this site can read.

**XSS (Cross-Site Scripting)** — sneaking a script into a page through a data field.
Blocked by escaping all output and by the **CSP**.

**CSP (Content-Security-Policy)** — an instruction to the browser about which scripts it
may run. Ours allows only this site's own scripts, each carrying a **nonce** — a
single-use random value regenerated on every page load, which an attacker cannot guess.

**Magic bytes** — the first few bytes of a file that reveal its true type. We check these
rather than trusting the filename, so `virus.jpg` that is really a script is rejected.

**Rate limit** — a cap on how often something may be done, e.g. 5 sign-in attempts per
minute per address.

**Honeypot** — an invisible form field. A person never fills it; an automated bot fills
everything. If it is filled, the submission is discarded.

**Firewall / ufw** — blocks all network ports except the ones you allow (here: SSH and
web).

**fail2ban** — watches the logs and temporarily bans addresses that keep failing to log
in.

**SSH / SSH key** — the secure remote login to the server. A **key** is a long
cryptographic file, far stronger than a password; password login is switched off.

**CDN (Content Delivery Network)** — someone else's servers hosting shared code. Common,
convenient, and avoided here: it would let a third party change code inside your dashboard
and would tell them whenever an official opened a page.

**Checksum (SHA-256 sum)** — a fingerprint recorded for each downloaded library so you can
prove the file has not changed. Ours are in `api/static/vendor/CHECKSUMS.txt`.

**`.env` file** — where passwords and secrets live. Never committed to version control,
readable only by the owner.

**Virtual environment (`.venv`)** — a private folder of Python libraries for this project
only, so it cannot be broken by anything else on the machine.

**Pinned dependency** — a library locked to one exact version, so the software cannot
change under you between installs.

**ULB (Urban Local Body)** — the municipal corporation or council: here Rewari MC, Bawal
MC, Dharuhera MC, plus a "Rural/Other" bucket.

**Pashu Aadhaar** — India's national livestock identifier: a 12-digit yellow polyurethane
ear tag linked to the central Bharat Pashudhan database. The app validates the 12-digit
format so this registry can line up with the national one later.

**Gaushala / nandi-shala / cattle pound** — cow shelter / bull shelter / municipal impound
facility.
