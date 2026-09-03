/* Charts + map for the admin overview.
 *
 * All data comes from /api/stats/*, which is aggregate-only, so nothing
 * personal is ever drawn here.
 *
 * Colour discipline, which is the whole point of this file's structure:
 *
 *   - Each chart owns ONE hue from the categorical palette, and that same hue
 *     is the coloured rule along the top of its card.  Two charts never speak
 *     in the same colour, so "the blue one" is an unambiguous reference in a
 *     meeting.
 *   - Green, amber and red are reserved.  They mean a percentage doing well,
 *     doing adequately, or doing badly, and they mean nothing else.  The only
 *     chart that uses them is tagged-vs-untagged by ULB, which IS a percentage.
 *   - Every chart is registered with GTInsight so that clicking its card can
 *     rebuild the same chart, live, at full screen size.
 */
(function () {
  "use strict";

  // Read the palette from the stylesheet rather than repeating hex codes here:
  // one edit to admin.css restyles the page and the charts together.
  var css = getComputedStyle(document.documentElement);
  function v(name, fallback) { return (css.getPropertyValue(name) || "").trim() || fallback; }

  // Earth palette, defined once in admin.css: hide, dun, dark hide, slate,
  // brick, husk, straw, stone.
  var C1 = v("--c1", "#8a5a2b"),
      C2 = v("--c2", "#b3762a"),
      C3 = v("--c3", "#6b4423"),
      C4 = v("--c4", "#54707a"),
      C5 = v("--c5", "#9c4a2f"),
      C6 = v("--c6", "#7d7a52"),
      C7 = v("--c7", "#c08a3e"),
      C8 = v("--c8", "#5a5750"),
      GOOD = v("--pct-good", "#15803d"),
      BAD = v("--pct-bad", "#b91c1c"),
      LINE = v("--line", "#dfe4e0");

  var q = window.GT_ULB ? "?ulb=" + window.GT_ULB : "";
  var qAmp = window.GT_ULB ? "&ulb=" + window.GT_ULB : "";

  Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.color = "#5d6b64";
  Chart.defaults.animation = { duration: 400 };

  function fade(hex, alpha) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
    if (!m) return hex;
    return "rgba(" + parseInt(m[1], 16) + "," + parseInt(m[2], 16) + "," + parseInt(m[3], 16) + "," + alpha + ")";
  }

  /* These are FUNCTIONS, not shared objects, and that is load-bearing.  Chart.js
     mutates the config it is handed — it caches resolved option proxies and
     animation state on the very objects you pass in.  Share one `scales.x`
     literal between the card's chart and the enlarged copy in the modal and the
     second instance dies with "this._fn is not a function".  Every call returns
     a brand-new object graph. */
  function xAxis() { return { grid: { color: LINE }, ticks: { maxRotation: 0, autoSkip: true } }; }
  function yAxis() { return { grid: { color: LINE }, beginAtZero: true, ticks: { precision: 0 } }; }
  function legendBottom() {
    return { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10, padding: 12 } } };
  }

  // Submit the ULB filter on change.  Done here rather than with an inline
  // onchange= attribute, which the Content-Security-Policy forbids.
  var filter = document.getElementById("ulb-filter");
  if (filter) {
    filter.querySelector("select").addEventListener("change", function () { filter.submit(); });
  }

  async function get(url) {
    var r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw new Error(url + " -> " + r.status);
    return r.json();
  }

  /* Build a chart on the page AND teach the insight modal how to build it
     again on a bigger canvas.  `make(canvas)` must be pure with respect to the
     canvas it is handed: it is called once for the card and once more, later,
     for the enlarged copy. */
  function mount(id, make) {
    var el = document.getElementById(id);
    if (el) make(el);
    if (window.GTInsight) window.GTInsight.chart(id, make);
  }

  // ---- registrations per day: the three-series chart ----------------------
  get("/api/stats/timeseries?days=30" + qAmp).then(function (d) {
    mount("c-daily", function (canvas) {
      return new Chart(canvas, {
        type: "line",
        data: {
          labels: d.labels.map(function (s) { return s.slice(5); }),
          datasets: [
            { label: "Animals", data: d.animals, borderColor: C1, backgroundColor: fade(C1, .12),
              fill: true, tension: .3, pointRadius: 0, borderWidth: 2 },
            { label: "Owners", data: d.owners, borderColor: C4, tension: .3, pointRadius: 0, borderWidth: 2 },
            { label: "Road sightings", data: d.sightings, borderColor: C5, borderDash: [4, 3],
              tension: .3, pointRadius: 0, borderWidth: 2 },
          ],
        },
        options: {
          maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
          scales: { x: xAxis(), y: yAxis() }, plugins: legendBottom(),
        },
      });
    });
  }).catch(console.error);

  // ---- sightings by hour: the ochre chart ---------------------------------
  get("/api/stats/hourly" + q).then(function (d) {
    mount("c-hourly", function (canvas) {
      return new Chart(canvas, {
        type: "bar",
        data: { labels: d.labels, datasets: [{ label: "Sightings", data: d.counts, backgroundColor: C2, borderRadius: 3 }] },
        options: {
          maintainAspectRatio: false, scales: { x: xAxis(), y: yAxis() },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { title: function (i) { return i[0].label + ":00 - " + i[0].label + ":59 IST"; } } },
          },
        },
      });
    });
  }).catch(console.error);

  // ---- tagged / untagged by ULB: the ONE chart allowed to use green and red,
  //      because it is a coverage percentage drawn as counts ------------------
  (function () {
    var rows = window.GT_BY_ULB || [];
    mount("c-ulb", function (canvas) {
      return new Chart(canvas, {
        type: "bar",
        data: {
          labels: rows.map(function (r) { return r.code; }),
          datasets: [
            { label: "Tagged", data: rows.map(function (r) { return r.tagged; }), backgroundColor: GOOD },
            { label: "Untagged", data: rows.map(function (r) { return r.animals - r.tagged; }),
              backgroundColor: fade(BAD, .75) },
          ],
        },
        options: {
          maintainAspectRatio: false,
          scales: {
            x: Object.assign(xAxis(), { stacked: true }),
            y: Object.assign(yAxis(), { stacked: true }),
          },
          plugins: Object.assign(legendBottom(), {
            tooltip: {
              callbacks: {
                footer: function (items) {
                  var row = rows[items[0].dataIndex];
                  if (!row || !row.animals) return "";
                  return Math.round(1000 * row.tagged / row.animals) / 10 + "% covered";
                },
              },
            },
          }),
        },
      });
    });
  })();

  // ---- species and sex: the categorical chart ------------------------------
  get("/api/stats/species_sex" + q).then(function (rows) {
    mount("c-species", function (canvas) {
      return new Chart(canvas, {
        type: "pie",
        data: {
          labels: rows.map(function (r) { return r.species + " · " + r.sex; }),
          datasets: [{
            data: rows.map(function (r) { return r.n; }),
            // Ordered dark / light / cool / warm so the first four slices --
            // which is all this chart usually has -- stay apart from each
            // other.  None of them green: a slice is a category, never a score.
            backgroundColor: [C3, C7, C4, C5, C6, C8, C2, C1],
            borderColor: "#fff", borderWidth: 2,
          }],
        },
        options: {
          // A solid pie, not a ring.  The hollow centre was reading as a piece
          // missing from the chart rather than as a design choice, and there is
          // nothing being shown in the middle that would earn the hole.
          maintainAspectRatio: false,
          layout: { padding: 2 },
          plugins: {
            legend: { position: "right", align: "center",
                      labels: { boxWidth: 10, boxHeight: 10, padding: 8 } },
            tooltip: {
              callbacks: {
                label: function (i) {
                  var total = i.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                  var pc = total ? Math.round(1000 * i.parsed / total) / 10 : 0;
                  return " " + i.label + ": " + i.parsed + " (" + pc + "%)";
                },
              },
            },
          },
        },
      });
    });
  }).catch(console.error);

  // ---- where the sightings are --------------------------------------------
  var mapEl = document.getElementById("map");
  if (mapEl && window.L) {
    var map = L.map(mapEl).setView([28.199, 76.619], 11);
    L.tileLayer(window.GT_TILES, { attribution: window.GT_ATTR, maxZoom: 18 }).addTo(map);
    var cluster = L.markerClusterGroup ? L.markerClusterGroup() : L.layerGroup();
    get("/api/stats/sightings_geo?days=30" + qAmp).then(function (points) {
      points.forEach(function (p) {
        L.circleMarker([p.lat, p.lng], {
          radius: Math.min(5 + p.n, 16), color: C5, weight: 1,
          fillColor: C5, fillOpacity: Math.min(.25 + p.n * 0.08, .8),
        }).bindPopup(p.n + " sighting(s)<br>last: " + p.last_seen.slice(0, 16).replace("T", " ")).addTo(cluster);
      });
      cluster.addTo(map);
      if (points.length) {
        map.fitBounds(points.map(function (p) { return [p.lat, p.lng]; }), { padding: [30, 30], maxZoom: 13 });
      }
    }).catch(console.error);
  }
})();
