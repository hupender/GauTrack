/* GauTrack field app: offline-first.
 *
 * Design rules:
 *  - Nothing is trusted on this side. The phone proposes; the server decides.
 *  - Every row this app creates carries a client-generated UUIDv7 so the upload
 *    is idempotent: retrying a batch forever can never duplicate a record.
 *  - Entries live in IndexedDB until the server acknowledges each one by id.
 */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };

  // ---------------------------------------------------------------- uuid v7
  function uuid7() {
    const ms = Date.now();
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    b[0] = (ms / 2 ** 40) & 0xff;
    b[1] = (ms / 2 ** 32) & 0xff;
    b[2] = (ms / 2 ** 24) & 0xff;
    b[3] = (ms / 2 ** 16) & 0xff;
    b[4] = (ms / 2 ** 8) & 0xff;
    b[5] = ms & 0xff;
    b[6] = 0x70 | (b[6] & 0x0f);            // version 7
    b[8] = 0x80 | (b[8] & 0x3f);            // variant 10
    const h = Array.from(b, function (x) { return x.toString(16).padStart(2, "0"); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  }

  // ------------------------------------------------------------- indexeddb
  let _db = null;
  function idb() {
    if (_db) return Promise.resolve(_db);
    return new Promise(function (res, rej) {
      const req = indexedDB.open("gautrack", 1);
      req.onupgradeneeded = function () {
        const d = req.result;
        if (!d.objectStoreNames.contains("queue")) d.createObjectStore("queue", { keyPath: "id" });
        if (!d.objectStoreNames.contains("photos")) d.createObjectStore("photos", { keyPath: "ref" });
        if (!d.objectStoreNames.contains("owners")) d.createObjectStore("owners", { keyPath: "id" });
        if (!d.objectStoreNames.contains("meta")) d.createObjectStore("meta", { keyPath: "k" });
      };
      req.onsuccess = function () { _db = req.result; res(_db); };
      req.onerror = function () { rej(req.error); };
    });
  }
  function tx(store, mode, fn) {
    return idb().then(function (d) {
      return new Promise(function (res, rej) {
        const t = d.transaction(store, mode);
        const s = t.objectStore(store);
        let out;
        try { out = fn(s); } catch (e) { rej(e); return; }
        t.oncomplete = function () { res(out && out.result !== undefined ? out.result : out); };
        t.onerror = function () { rej(t.error); };
      });
    });
  }
  const put = function (store, val) { return tx(store, "readwrite", function (s) { return s.put(val); }); };
  const del = function (store, key) { return tx(store, "readwrite", function (s) { return s.delete(key); }); };
  const getAll = function (store) { return tx(store, "readonly", function (s) { return s.getAll(); }); };
  const get1 = function (store, key) { return tx(store, "readonly", function (s) { return s.get(key); }); };

  async function meta(k, v) {
    if (v === undefined) { const r = await get1("meta", k); return r ? r.v : null; }
    await put("meta", { k: k, v: v });
    return v;
  }

  // ------------------------------------------------------------------- api
  let CSRF = "";
  async function api(method, url, body, raw) {
    const headers = { "X-Requested-With": "GauTrack" };
    if (CSRF) headers["X-CSRF-Token"] = CSRF;
    if (body && !raw) headers["Content-Type"] = "application/json";
    const res = await fetch(url, {
      method: method,
      credentials: "same-origin",
      headers: headers,
      body: raw ? body : (body ? JSON.stringify(body) : undefined),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) {
      const d = data && data.detail;
      const err = new Error((d && (d.reason || d)) || res.statusText);
      err.status = res.status;
      err.detail = d;
      throw err;
    }
    return data;
  }

  // ----------------------------------------------------------------- state
  const state = {
    me: null,
    deviceId: null,
    shelters: [],
    gps: {},                 // per-form fix
    photos: {},              // per-form photo ref
    road: { animal: null, noTag: false },
    species: { an: "cattle", imp: "cattle" },
    sex: { an: "female", imp: "male" },
    age: { an: "adult", imp: "adult" },
  };

  // ----------------------------------------------------------------- utils
  function msg(where, text, cls) {
    const el = $(where);
    if (!el) return;
    el.innerHTML = text ? '<div class="msg ' + (cls || "ok") + '">' + escapeHtml(text) + "</div>" : "";
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function show(screen) {
    document.querySelectorAll(".screen").forEach(function (s) { s.classList.remove("on"); });
    $("s-" + screen).classList.add("on");
    window.scrollTo(0, 0);
    const authed = !!state.me;
    $("btn-home").hidden = !authed || screen === "home";
    $("btn-admin").hidden = !authed;
    $("btn-logout").hidden = !authed;
  }

  // ------------------------------------------------------------------- GPS
  function grabGps(key, outEl) {
    const el = $(outEl);
    if (!navigator.geolocation) {
      el.innerHTML = '<span class="bad">GPS not available in this browser.</span>';
      return;
    }
    if (!window.isSecureContext) {
      // Chrome blocks geolocation on plain http from a LAN IP. Degrade, do not break.
      el.innerHTML =
        '<span class="bad">GPS blocked: this page is not on https.</span>' +
        '<span class="muted"> You can still save: the entry is stored without coordinates.' +
        " Open the app over https (see README, `make dev-tls`) to record location.</span>";
      return;
    }
    el.innerHTML = "<span>Getting location…</span>";
    navigator.geolocation.getCurrentPosition(
      function (p) {
        state.gps[key] = { lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy };
        const good = p.coords.accuracy <= 25;
        el.innerHTML =
          '<span class="' + (good ? "ok" : "bad") + '">' +
          p.coords.latitude.toFixed(5) + ", " + p.coords.longitude.toFixed(5) + "</span>" +
          '<span class="muted">accuracy ±' + Math.round(p.coords.accuracy) + " m" +
          (good ? "" : ": move to open sky and retry") + "</span>";
      },
      function (e) {
        el.innerHTML = '<span class="bad">No location (' + escapeHtml(e.message) + ")</span>";
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 }
    );
  }

  // ----------------------------------------------------------------- photos
  /* crypto.subtle exists only in a "secure context" (https, or localhost) - on a plain
     http://<lan-ip> demo (see runbook step 9) it is undefined and .digest would throw,
     which used to abort the whole photo capture and block the form. The server always
     computes its own SHA-256 of the uploaded bytes and stores that (photos.py store_photo);
     the client hash sent alongside it is only an extra cross-check, used when present
     (routes/photos_routes.py, photos.py). So on an insecure context we skip the client-side
     hash entirely rather than fail the capture - server-side integrity is unaffected. */
  async function sha256Hex(buf) {
    if (!window.crypto || !window.crypto.subtle) return null;
    const d = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(d), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  /* Downscale to <=1600px and <=400KB JPEG before it ever touches the queue -
     a 12MP phone photo would otherwise make offline sync unusable on 2G. */
  function downscale(file) {
    return new Promise(function (resolve, reject) {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = function () {
        URL.revokeObjectURL(url);
        const max = 1600;
        let w = img.naturalWidth, h = img.naturalHeight;
        if (w > max || h > max) {
          const r = Math.min(max / w, max / h);
          w = Math.round(w * r); h = Math.round(h * r);
        }
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, 0, 0, w, h);
        (function attempt(q) {
          c.toBlob(function (blob) {
            if (!blob) { reject(new Error("could not encode image")); return; }
            if (blob.size <= 400 * 1024 || q <= 0.4) { resolve(blob); return; }
            attempt(q - 0.1);
          }, "image/jpeg", q);
        })(0.85);
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("not an image")); };
      img.src = url;
    });
  }

  async function capture(inputId, thumbId, key) {
    const f = $(inputId).files[0];
    if (!f) return;
    try {
      const blob = await downscale(f);
      const buf = await blob.arrayBuffer();
      const ref = uuid7();
      await put("photos", { ref: ref, blob: blob, sha256: await sha256Hex(buf), serverId: null });
      state.photos[key] = ref;
      const t = $(thumbId);
      if (t) { t.src = URL.createObjectURL(blob); t.hidden = false; }
    } catch (e) {
      alert("Could not use that photo: " + e.message);
    }
  }

  async function uploadPhoto(ref) {
    const rec = await get1("photos", ref);
    if (!rec) return null;
    if (rec.serverId) return rec.serverId;
    const fd = new FormData();
    fd.append("file", rec.blob, "photo.jpg");
    // sha256 is null when captured on an insecure context (see sha256Hex above); the
    // server always computes its own hash of the uploaded bytes regardless (photos.py),
    // so omitting this is a reduced client-side check, not a server-side one.
    if (rec.sha256) fd.append("sha256", rec.sha256);
    const out = await api("POST", "/api/photos", fd, true);
    rec.serverId = out.id;
    await put("photos", rec);
    return out.id;
  }

  // ------------------------------------------------------------------ queue
  async function enqueue(kind, data, photoKeys) {
    const item = {
      id: uuid7(),
      kind: kind,
      data: data,
      photoRefs: photoKeys || {},           // { field: ref }
      status: "pending",
      reason: null,
      created: new Date().toISOString(),
    };
    await put("queue", item);
    await refreshBadge();
    scheduleSync();
    return item;
  }

  async function pendingCount() {
    const all = await getAll("queue");
    return all.filter(function (i) { return i.status === "pending"; }).length;
  }

  async function refreshBadge() {
    const n = await pendingCount();
    const b = $("sync-badge");
    b.textContent = n;
    b.className = "badge" + (n ? " pend" : "");
    const sub = $("home-sync-sub");
    if (sub) sub.textContent = n + " pending";
  }

  let syncing = false;
  let syncTimer = null;
  function scheduleSync() {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(function () { syncNow(true); }, 1500);
  }

  async function syncNow(quiet) {
    if (syncing || !state.me) return;
    if (!navigator.onLine) { if (!quiet) msg("sync-msg", "No network: will retry automatically.", "warn"); return; }
    syncing = true;
    try {
      const all = await getAll("queue");
      const pending = all.filter(function (i) { return i.status === "pending"; }).slice(0, 200);
      if (!pending.length) { if (!quiet) msg("sync-msg", "Everything is synced", "ok"); return; }

      // photos first: a queued row may reference a photo that is not uploaded yet
      for (const item of pending) {
        for (const field of Object.keys(item.photoRefs || {})) {
          try {
            const sid = await uploadPhoto(item.photoRefs[field]);
            if (sid) {
              if (field === "photo_ids") {
                item.data.photo_ids = (item.data.photo_ids || []).concat([sid]);
              } else {
                item.data[field] = sid;
              }
            }
          } catch (e) {
            item.reason = "photo upload failed: " + e.message;
          }
        }
        await put("queue", item);
      }

      const body = {
        device_id: state.deviceId,
        device_label: navigator.userAgent.slice(0, 60),
        items: pending.map(function (i) {
          return { kind: i.kind, id: i.data.id || i.id, data: i.data, device_id: state.deviceId };
        }),
      };
      const out = await api("POST", "/api/sync", body);
      const byId = {};
      out.results.forEach(function (r) { byId[r.id] = r; });

      for (const item of pending) {
        const r = byId[item.data.id || item.id];
        if (!r) continue;                                  // no ack -> keep it queued
        if (r.status === "created" || r.status === "duplicate") {
          item.status = "done";
          item.reason = r.status === "duplicate" ? "already on the server" : null;
        } else {
          item.status = "error";
          item.reason = (r.status === "conflict" ? "Conflict: " : "Rejected: ") + (r.reason || "");
          if (r.existing) item.existing = r.existing;
        }
        await put("queue", item);
      }
      await refreshBadge();
      await renderQueue();
      if (!quiet) msg("sync-msg", "Uploaded " + out.results.length + " entries.", "ok");
    } catch (e) {
      if (!quiet) msg("sync-msg", "Upload failed: " + e.message, "err");
      if (e.status === 401) { state.me = null; show("login"); }
    } finally {
      syncing = false;
    }
  }

  async function renderQueue() {
    const all = (await getAll("queue")).sort(function (a, b) { return a.created < b.created ? 1 : -1; });
    const ul = $("sy-list");
    if (!all.length) { ul.innerHTML = '<li class="muted">Everything is synced</li>'; return; }
    ul.innerHTML = all.map(function (i) {
      const cls = i.status === "done" ? "good" : (i.status === "error" ? "bad" : "warn");
      const label = i.kind + " · " + (i.data.name || i.data.tag_id || i.data.type || i.data.id.slice(0, 8));
      return "<li><span class='pill " + cls + "'>" + i.status + "</span> " + escapeHtml(label) +
        "<div class='small muted'>" + escapeHtml(i.created.slice(0, 19).replace("T", " ")) +
        (i.reason ? ": " + escapeHtml(i.reason) : "") +
        (i.existing ? ": existing: " + escapeHtml(i.existing.tag_id || i.existing.id) : "") + "</div></li>";
    }).join("");
  }

  // ------------------------------------------------------------ owner cache
  async function cacheOwners() {
    if (!navigator.onLine) return;
    try {
      const out = await api("GET", "/api/owners?limit=200");
      for (const o of out.items) await put("owners", o);
    } catch (e) { /* offline is fine */ }
  }

  async function ownerOptions(filter) {
    const owners = await getAll("owners");
    const f = (filter || "").trim().toLowerCase();
    const rows = owners.filter(function (o) {
      if (!f) return true;
      return (o.name || "").toLowerCase().includes(f) ||
             (o.ward_or_village || "").toLowerCase().includes(f) ||
             (o.phone || "").includes(f);
    }).slice(0, 60);
    $("an-owner").innerHTML = rows.map(function (o) {
      return '<option value="' + o.id + '">' + escapeHtml(o.name) +
        (o.ward_or_village ? ": " + escapeHtml(o.ward_or_village) : "") + "</option>";
    }).join("") || '<option value="">No cached owners: register one first</option>';
  }

  // ---------------------------------------------------------------- session
  async function loadMe() {
    try {
      const me = await api("GET", "/api/me");
      state.me = me;
      CSRF = me.csrf_token;
      $("who").textContent = me.full_name + " · " + (me.ulb_name || me.role);
      $("home-user").textContent = me.username + " (" + me.role + ")";
      $("demo-banner").hidden = !me.demo;
      await cacheOwners();
      await loadShelters();
      show("home");
      return true;
    } catch (e) {
      state.me = null;
      show("login");
      return false;
    }
  }

  async function loadShelters() {
    try {
      const rows = await api("GET", "/api/stats/shelters");
      state.shelters = rows;
      $("imp-shelter").innerHTML = rows.map(function (s) {
        return '<option value="' + s.id + '">' + escapeHtml(s.name) + " (" + s.current_count + "/" + s.capacity + ")</option>";
      }).join("");
      await meta("shelters", rows);
    } catch (e) {
      const cached = await meta("shelters");
      if (cached) {
        state.shelters = cached;
        $("imp-shelter").innerHTML = cached.map(function (s) {
          return '<option value="' + s.id + '">' + escapeHtml(s.name) + "</option>";
        }).join("");
      }
    }
  }

  // ------------------------------------------------------------------ wiring
  function chips(groupId, key, bucket) {
    const g = $(groupId);
    if (!g) return;
    g.addEventListener("click", function (e) {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      g.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
      bucket[key] = b.dataset.v;
    });
  }

  function init() {
    chips("an-species", "an", state.species);
    chips("an-sex", "an", state.sex);
    chips("an-age", "an", state.age);
    chips("imp-species", "imp", state.species);
    chips("imp-sex", "imp", state.sex);
    chips("imp-age", "imp", state.age);

    $("li-go").addEventListener("click", async function () {
      msg("login-msg", "Signing in…", "ok");
      try {
        const out = await api("POST", "/api/auth/login", {
          username: $("li-user").value.trim().toLowerCase(),
          password: $("li-pass").value,
          totp_code: $("li-totp").value || null,
        });
        CSRF = out.csrf_token;
        $("li-pass").value = "";
        msg("login-msg", "", "ok");
        await loadMe();
      } catch (e) {
        msg("login-msg", e.message || "Sign in failed", "err");
      }
    });

    $("btn-logout").addEventListener("click", async function () {
      router.navigate("/admin");
    });
    $("btn-admin").addEventListener("click", async function () {
      try { await api("POST", "/admin"); } catch (e) { /* ignore */ }
      state.me = null;
      show("admin");
    });
    $("btn-home").addEventListener("click", function () { show("home"); });

    document.querySelectorAll("[data-go]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const to = b.dataset.go;
        show(to);
        if (to === "owner") grabGps("ow", "ow-gps");
        if (to === "animal") { grabGps("an", "an-gps"); await cacheOwners(); await ownerOptions(""); }
        if (to === "road") grabGps("rd", "rd-gps");
        if (to === "impound") grabGps("imp", "imp-gps");
        if (to === "sync") { await renderQueue(); }
      });
    });

    document.querySelectorAll("[data-gps]").forEach(function (b) {
      b.addEventListener("click", function () { grabGps(b.dataset.gps, b.dataset.gps + "-gps"); });
    });

    $("ow-photo").addEventListener("change", function () { capture("ow-photo", "ow-thumb", "owner"); });
    $("an-photo").addEventListener("change", function () { capture("an-photo", "an-thumb", "animal"); });
    $("an-muzzle").addEventListener("change", function () { capture("an-muzzle", null, "muzzle"); });
    $("rd-photo").addEventListener("change", function () { capture("rd-photo", "rd-thumb", "road"); });
    $("imp-photo").addEventListener("change", function () { capture("imp-photo", "imp-thumb", "imp"); });

    $("an-owner-search").addEventListener("input", function () { ownerOptions(this.value); });

    // A blank box means "not asked / not known", which must reach the server as
    // null, not as 0.  A 0 would read as "this keeper declared zero cattle".
    function optNum(id, opts) {
      const raw = $(id).value.trim();
      if (!raw) return null;
      const n = Number(raw);
      if (!isFinite(n) || n < 0) return NaN;            // caller reports the error
      if (opts && opts.max !== undefined && n > opts.max) return NaN;
      return opts && opts.int ? Math.round(n) : n;
    }

    function optText(id) { return $(id).value.trim() || null; }

    // The two optional identification marks, joined for display.
    function marks(a) {
      return [a.identification_mark_1, a.identification_mark_2].filter(Boolean).join(" · ");
    }

    // ---- save owner -----------------------------------------------------
    $("ow-save").addEventListener("click", async function () {
      const name = $("ow-name").value.trim();
      if (!name) { msg("owner-msg", "Name is required.", "err"); return; }
      const declared = optNum("ow-declared", { int: true, max: 2000 });
      if (isNaN(declared)) { msg("owner-msg", "Cattle owned must be a whole number, 0 to 2000.", "err"); return; }
      const area = optNum("ow-area", { max: 1000000 });
      if (isNaN(area)) { msg("owner-msg", "Premises area must be a number in square yards.", "err"); return; }
      const g = state.gps.ow || {};
      const id = uuid7();
      const data = {
        id: id,
        name: name,
        relation_name: $("ow-relation").value.trim() || null,
        phone: $("ow-phone").value.trim() || null,
        ward_or_village: $("ow-village").value.trim() || null,
        keeper_type: $("ow-keeper").value,
        self_declared_cattle_count: declared,
        premises_area_sq_yards: area,
        address: $("ow-address").value.trim() || null,
        notes: $("ow-notes").value.trim() || null,
        lat: g.lat || null, lng: g.lng || null, gps_accuracy_m: g.acc || null,
      };
      const refs = {};
      if (state.photos.owner) refs.photo_id = state.photos.owner;
      await enqueue("owner", data, refs);
      await put("owners", { id: id, name: name, ward_or_village: data.ward_or_village, phone: data.phone });
      msg("owner-msg", "Saved. Now add this owner's animals.", "ok");
      ["ow-name", "ow-relation", "ow-phone", "ow-address", "ow-notes",
       "ow-declared", "ow-area"].forEach(function (i) { $(i).value = ""; });
      $("ow-thumb").hidden = true; state.photos.owner = null; $("ow-photo").value = "";
      await ownerOptions("");
      $("an-owner").value = id;
      setTimeout(function () { show("animal"); grabGps("an", "an-gps"); }, 600);
    });

    // ---- save animal ----------------------------------------------------
    $("an-save").addEventListener("click", async function () {
      const ownerId = $("an-owner").value;
      if (!ownerId) { msg("animal-msg", "Choose an owner first.", "err"); return; }
      if (!state.photos.animal) { msg("animal-msg", "A photograph is required.", "err"); return; }
      const tagType = $("an-tagtype").value;
      const tag = $("an-tag").value.trim().toUpperCase();
      if (tagType === "pashu_aadhaar_12" && tag && !/^\d{12}$/.test(tag)) {
        msg("animal-msg", "A Pashu Aadhaar tag is exactly 12 digits.", "err"); return;
      }
      const ageYears = optNum("an-age-years", { max: 40 });
      if (isNaN(ageYears)) { msg("animal-msg", "Age must be a number of years between 0 and 40.", "err"); return; }
      const g = state.gps.an || {};
      const data = {
        id: uuid7(),
        owner_id: ownerId,
        species: state.species.an,
        sex: state.sex.an,
        age_class: state.age.an,
        age_years: ageYears,
        breed: $("an-breed").value.trim() || null,
        colour_markings: $("an-colour").value.trim() || null,
        identification_mark_1: optText("an-mark1"),
        identification_mark_2: optText("an-mark2"),
        tag_id: tag || null,
        tag_type: tag ? tagType : "none",
        lat: g.lat || null, lng: g.lng || null,
      };
      const refs = { photo_id: state.photos.animal };
      if (state.photos.muzzle) refs.muzzle_photo_id = state.photos.muzzle;
      await enqueue("animal", data, refs);
      msg("animal-msg", "Animal saved to the upload queue.", "ok");
      ["an-tag", "an-colour", "an-mark1", "an-mark2", "an-age-years"]
        .forEach(function (i) { $(i).value = ""; });
      $("an-thumb").hidden = true; state.photos.animal = null; state.photos.muzzle = null;
      $("an-photo").value = ""; $("an-muzzle").value = "";
    });

    // ---- animal on road -------------------------------------------------
    $("rd-lookup").addEventListener("click", async function () {
      const tag = $("rd-tag").value.trim().toUpperCase();
      if (!tag) return;
      if (!navigator.onLine) { msg("road-msg", "Tag lookup needs a network. Use 'No tag' and note the number.", "warn"); return; }
      try {
        const out = await api("GET", "/api/lookup/tag/" + encodeURIComponent(tag));
        state.road.animal = out;
        state.road.noTag = false;
        $("rd-found").innerHTML =
          '<div class="msg ok"><strong>' + escapeHtml(out.animal.species) + " · " + escapeHtml(out.animal.sex) +
          "</strong>: " + escapeHtml(out.owner ? out.owner.name : "no owner on record") +
          (out.owner && out.owner.phone ? " · " + escapeHtml(out.owner.phone) + (out.owner.phone_masked ? " (masked: other ULB)" : "") : "") +
          (marks(out.animal) ? "<br>Marks: " + escapeHtml(marks(out.animal)) : "") +
          "<br>Offences so far: <strong>" + escapeHtml(String(out.offence_count)) + "</strong>" +
          (out.in_scope ? "" : " · <span class='pill warn'>registered in another ULB</span>") + "</div>";
      } catch (e) {
        state.road.animal = null;
        $("rd-found").innerHTML = '<div class="msg err">No animal found with that tag.</div>';
      }
    });

    $("rd-notag").addEventListener("click", function () {
      state.road.noTag = true;
      state.road.animal = null;
      $("rd-found").innerHTML = '<div class="msg warn">Recording as an untagged animal with no owner.</div>';
    });

    async function roadAction(kind) {
      if (!state.photos.road) { msg("road-msg", "A photograph is required.", "err"); return; }
      if (!state.road.animal && !state.road.noTag) {
        msg("road-msg", "Look up the tag, or choose 'No tag'.", "err"); return;
      }
      const g = state.gps.rd || {};
      const note = $("rd-notes").value.trim() || null;
      let animalId = state.road.animal ? state.road.animal.animal.id : null;

      if (!animalId) {
        // untagged: register the animal first, in the same queue, so the event
        // that follows has something to point at once both are uploaded.
        animalId = uuid7();
        await enqueue("animal", {
          id: animalId, owner_id: null, species: "cattle", sex: "unknown", age_class: "adult",
          colour_markings: note, lat: g.lat || null, lng: g.lng || null, status: "registered",
        }, { photo_id: state.photos.road });
      }

      const base = {
        animal_id: animalId,
        lat: g.lat || null, lng: g.lng || null, gps_accuracy_m: g.acc || null,
        occurred_at: new Date().toISOString(),
      };
      await enqueue("event", Object.assign({ id: uuid7(), type: "sighting_road",
        payload: { note: note, action: kind, road_side: true } }, base),
        state.photos.road ? { photo_ids: state.photos.road } : {});

      if (kind === "impound") {
        await enqueue("event", Object.assign({ id: uuid7(), type: "impound",
          payload: { note: note } }, base), {});
      } else if (kind === "fine") {
        if (state.road.animal && !state.road.animal.animal.owner_id) {
          msg("road-msg", "This animal has no registered owner: a fine needs an owner. Impound instead.", "err");
          return;
        }
        await enqueue("event", Object.assign({ id: uuid7(), type: "fine_issued",
          payload: { note: note } }, base), {});
      }

      msg("road-msg", kind === "warn" ? "Sighting recorded and owner warned."
        : kind === "impound" ? "Sighting + impound queued." : "Sighting + fine queued.", "ok");
      $("rd-tag").value = ""; $("rd-notes").value = ""; $("rd-found").innerHTML = "";
      $("rd-thumb").hidden = true; state.photos.road = null; $("rd-photo").value = "";
      state.road = { animal: null, noTag: false };
    }
    $("rd-warn").addEventListener("click", function () { roadAction("warn"); });
    $("rd-impound").addEventListener("click", function () { roadAction("impound"); });
    $("rd-fine").addEventListener("click", function () { roadAction("fine"); });

    // ---- impound untagged ----------------------------------------------
    $("imp-save").addEventListener("click", async function () {
      if (!state.photos.imp) { msg("imp-msg", "A photograph is required.", "err"); return; }
      const impAge = optNum("imp-age-years", { max: 40 });
      if (isNaN(impAge)) { msg("imp-msg", "Age must be a number of years between 0 and 40.", "err"); return; }
      const g = state.gps.imp || {};
      const shelterId = Number($("imp-shelter").value) || null;
      const animalId = uuid7();
      await enqueue("animal", {
        id: animalId, owner_id: null,
        species: state.species.imp, sex: state.sex.imp, age_class: state.age.imp,
        age_years: impAge,
        colour_markings: $("imp-colour").value.trim() || null,
        identification_mark_1: optText("imp-mark1"),
        identification_mark_2: optText("imp-mark2"),
        lat: g.lat || null, lng: g.lng || null,
      }, { photo_id: state.photos.imp });

      const base = { animal_id: animalId, lat: g.lat || null, lng: g.lng || null,
                     occurred_at: new Date().toISOString() };
      await enqueue("event", Object.assign({ id: uuid7(), type: "impound",
        payload: { stray: true } }, base), {});
      if (shelterId) {
        await enqueue("event", Object.assign({ id: uuid7(), type: "gaushala_intake",
          payload: { shelter_id: shelterId } }, base), {});
      }
      msg("imp-msg", "Stray impounded and queued for upload.", "ok");
      ["imp-colour", "imp-mark1", "imp-mark2", "imp-age-years"]
        .forEach(function (i) { $(i).value = ""; });
      $("imp-thumb").hidden = true;
      state.photos.imp = null; $("imp-photo").value = "";
    });

    // ---- lookup ---------------------------------------------------------
    $("lk-go").addEventListener("click", async function () {
      const tag = $("lk-tag").value.trim().toUpperCase();
      if (!tag) return;
      if (!navigator.onLine) { $("lk-out").innerHTML = '<div class="msg warn">Tag lookup needs a network.</div>'; return; }
      try {
        const out = await api("GET", "/api/lookup/tag/" + encodeURIComponent(tag));
        const a = out.animal, o = out.owner;
        $("lk-out").innerHTML =
          '<div class="card" style="margin-top:.6rem">' +
          "<h2 class='mono'>" + escapeHtml(a.tag_id || "untagged") + "</h2>" +
          "<p>" + escapeHtml(a.species) + " · " + escapeHtml(a.sex) + " · " + escapeHtml(a.age_class) +
          (a.age_years ? " · " + escapeHtml(String(a.age_years)) + " yr" : "") +
          " · " + escapeHtml(a.breed || "") + "<br><span class='pill'>" + escapeHtml(a.status) + "</span></p>" +
          // The marks are the point of a lookup on a half-removed tag: they let
          // the officer confirm this record really is the animal in front of them.
          (marks(a) ? "<p class='small'><strong>Marks:</strong> " + escapeHtml(marks(a)) + "</p>" : "") +
          (o ? "<p><strong>" + escapeHtml(o.name) + "</strong><br>" +
               escapeHtml(o.ward_or_village || "") + "<br><span class='mono'>" + escapeHtml(o.phone || "-") + "</span>" +
               (o.phone_masked ? " <span class='pill warn'>masked</span>" : "") + "</p>"
             : "<p class='pill warn'>No owner on record</p>") +
          "<p>Offences: <strong>" + escapeHtml(String(out.offence_count)) + "</strong></p>" +
          "<ul class='list'>" + out.recent_events.map(function (e) {
            return "<li><span class='pill mute'>" + escapeHtml(e.type) + "</span> " +
              escapeHtml(e.occurred_at.slice(0, 16).replace("T", " ")) + "</li>";
          }).join("") + "</ul></div>";
      } catch (e) {
        $("lk-out").innerHTML = '<div class="msg err">No animal found with that tag.</div>';
      }
    });

    // ---- sync screen ----------------------------------------------------
    $("sy-now").addEventListener("click", function () { syncNow(false); });
    $("sy-clear-done").addEventListener("click", async function () {
      const all = await getAll("queue");
      for (const i of all) if (i.status === "done") await del("queue", i.id);
      await renderQueue();
    });

    // ---- connectivity ---------------------------------------------------
    function online() {
      $("offline-bar").hidden = navigator.onLine;
      if (navigator.onLine) scheduleSync();
    }
    window.addEventListener("online", online);
    window.addEventListener("offline", online);
    online();
    setInterval(function () { syncNow(true); }, 60000);
  }

  // ------------------------------------------------------------------ boot
  (async function boot() {
    init();
    state.deviceId = await meta("device_id") || await meta("device_id", uuid7());
    await refreshBadge();
    await loadMe();
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).catch(function (e) {
        console.warn("service worker not registered:", e.message);
      });
    }
  })();
})();
