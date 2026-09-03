/* The CM screen's single chart.
 *
 * Kept in its own file rather than inline so it can register itself with
 * GTInsight: clicking the card rebuilds this exact chart on a full-screen
 * canvas, which an inline <script> could not do without re-declaring the
 * config a second time and letting the two drift apart.
 */
(function () {
  "use strict";
  var css = getComputedStyle(document.documentElement);
  function v(n, f) { return (css.getPropertyValue(n) || "").trim() || f; }

  var H = window.GT_HOURLY || { labels: [], counts: [] };
  var OCHRE = v("--c2", "#b3762a");
  var LINE = v("--line", "#dfe4e0");

  Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.color = "#5d6b64";

  function make(canvas) {
    return new Chart(canvas, {
      type: "bar",
      data: { labels: H.labels, datasets: [{ label: "Sightings", data: H.counts, backgroundColor: OCHRE, borderRadius: 3 }] },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: function (i) { return i[0].label + ":00 - " + i[0].label + ":59 IST"; } } },
        },
        scales: {
          x: { grid: { color: LINE }, ticks: { maxRotation: 0, autoSkip: true } },
          y: { grid: { color: LINE }, beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  }

  var el = document.getElementById("c-hourly");
  if (el) make(el);
  if (window.GTInsight) window.GTInsight.chart("c-hourly", make);
})();
