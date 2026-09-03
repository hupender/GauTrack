/* Insight cards.
 *
 * Any element carrying data-insight becomes clickable: it detaches from the
 * page, flies to the centre of the screen and enlarges.  That enlarged card is
 * what you get first, because "show me that bigger" is what clicking a card
 * means to everyone.  Behind it is a second face that explains the number —
 * what it counts, which table and condition it came from, and a link that
 * opens exactly those rows in the registry — reached with the button on the
 * card and left again with the button on the explanation.
 *
 * Three constraints shaped the implementation:
 *
 *  1. The KPI strip is re-swapped by HTMX every 60 seconds, so no handler may
 *     be bound to a card.  Everything is delegated from `document`, which means
 *     a freshly swapped card is live the instant it lands.
 *  2. The Content-Security-Policy forbids inline script, so this file is loaded
 *     with a <script src> and reads its content from data- attributes that the
 *     server rendered.  Nothing here evaluates a string.
 *  3. Chart cards must show a *live* chart when enlarged, not a screenshot.
 *     A chart registers a factory here (GTInsight.chart) and the modal calls it
 *     again against the cloned canvas, so the big version is a real Chart.js
 *     instance with its own tooltips.
 */
(function () {
  "use strict";

  var FACTORIES = {};   // canvas id -> function(canvasEl) -> Chart instance
  var open = null;      // { stage, backdrop, source, chart, onKey }
  var seq = 0;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* The back face.  Everything on it came from the server as a data- attribute;
     nothing is derived in the browser, so the explanation cannot drift away
     from what the query actually did. */
  function backFace(card) {
    var d = card.dataset;
    var link = d.insLink || "";
    var api = d.insApi || "";
    var rows = "";
    if (d.insSource) rows += "<dt>Table and condition</dt><dd>" + esc(d.insSource) + "</dd>";
    if (d.insScope) rows += "<dt>Scope applied</dt><dd>" + esc(d.insScope) + "</dd>";
    if (api) rows += "<dt>Endpoint</dt><dd>" + esc(api) + "</dd>";

    return '' +
      '<p class="zoom-kicker">' + esc(d.insKicker || "Where this comes from") + "</p>" +
      "<h3>" + esc(d.insTitle || "") + "</h3>" +
      '<div class="zoom-body">' +
        '<p class="zoom-what">' + esc(d.insWhat || "") + "</p>" +
        (rows ? '<dl class="zoom-src">' + rows + "</dl>" : "") +
        (d.insCaveat ? '<p class="legend" style="margin-top:0">' + esc(d.insCaveat) + "</p>" : "") +
      "</div>" +
      '<div class="zoom-bar">' +
        (link ? '<a class="btn" href="' + esc(link) + '">' +
                esc(d.insLinkLabel || "Open these records") + "</a>" : "") +
        (api ? '<a class="btn sec" href="' + esc(api) + '">View the raw JSON</a>' : "") +
        '<span class="grow"></span>' +
        '<button type="button" class="sec" data-zoom-flip>Back to the card</button>' +
        '<button type="button" class="sec" data-zoom-close>Close</button>' +
      "</div>";
  }

  /* The front face is the card the user clicked, enlarged.  Cloning keeps it
     identical to what they were just looking at; only canvas ids are rewritten
     so the clone cannot collide with the original still on the page. */
  function frontFace(card) {
    var clone = card.cloneNode(true);
    clone.removeAttribute("data-insight");
    // Keep every class EXCEPT `card` — the zoom face already draws the card
    // chrome, but `kpi` has to survive or the enlarged number would come out
    // at its 165px-wide size.
    var kept = (card.getAttribute("class") || "").split(/\s+/)
      .filter(function (c) { return c && c !== "card"; }).join(" ");
    var canvases = clone.querySelectorAll("canvas");
    var mapping = [];
    Array.prototype.forEach.call(canvases, function (c) {
      var original = c.id;
      c.id = original + "-zoom-" + (++seq);
      // Chart.js writes the card-sized backing store and an inline width/height
      // onto the canvas it owns.  The clone inherits both, so clear them or the
      // enlarged chart starts life locked to the small card's dimensions.
      c.removeAttribute("style");
      c.removeAttribute("width");
      c.removeAttribute("height");
      mapping.push({ from: original, to: c.id });
    });
    return {
      html: '<div class="zoom-body ' + kept + '">' + clone.innerHTML + "</div>" +
            '<div class="zoom-bar"><span class="grow"></span>' +
            '<button type="button" class="sec" data-zoom-flip>Where does this number come from?</button>' +
            '<button type="button" class="sec" data-zoom-close>Close</button></div>',
      canvases: mapping,
    };
  }

  function targetRect() {
    var w = Math.min(960, window.innerWidth * 0.92);
    var h = Math.min(660, window.innerHeight * 0.86);
    return { left: (window.innerWidth - w) / 2, top: (window.innerHeight - h) / 2, width: w, height: h };
  }

  function close() {
    if (!open) return;
    var o = open;
    open = null;
    document.removeEventListener("keydown", o.onKey);
    if (o.chart) { try { o.chart.destroy(); } catch (e) { /* already gone */ } }
    o.card.classList.remove("flipped");
    // `visibility:hidden` keeps the source in the layout, so it can still be
    // measured to fly the card back to exactly where it came from.
    var r = o.source.getBoundingClientRect();
    o.stage.style.top = r.top + "px";
    o.stage.style.left = r.left + "px";
    o.stage.style.width = r.width + "px";
    o.stage.style.height = r.height + "px";
    o.backdrop.classList.remove("on");
    window.setTimeout(function () {
      o.source.classList.remove("is-source");
      if (o.stage.parentNode) o.stage.parentNode.removeChild(o.stage);
      if (o.backdrop.parentNode) o.backdrop.parentNode.removeChild(o.backdrop);
      o.source.focus({ preventScroll: true });
    }, 430);
  }

  function openCard(card) {
    if (open) close();

    var start = card.getBoundingClientRect();
    var backdrop = document.createElement("div");
    backdrop.className = "zoom-backdrop";

    var stage = document.createElement("div");
    stage.className = "zoom-stage";
    stage.style.top = start.top + "px";
    stage.style.left = start.left + "px";
    stage.style.width = start.width + "px";
    stage.style.height = start.height + "px";
    var front = frontFace(card);
    var inner = document.createElement("div");
    inner.className = "zoom-card";
    inner.setAttribute("role", "dialog");
    inner.setAttribute("aria-modal", "true");
    inner.setAttribute("aria-label", card.dataset.insTitle || "Detail");
    inner.innerHTML =
      '<div class="zoom-face front card">' + front.html + "</div>" +
      '<div class="zoom-face back card">' + backFace(card) + "</div>";
    stage.appendChild(inner);

    document.body.appendChild(backdrop);
    document.body.appendChild(stage);

    // Rebuild any chart the clone carries, against the cloned canvas.
    var chart = null;
    front.canvases.forEach(function (m) {
      var factory = FACTORIES[m.from];
      var canvas = stage.querySelector("#" + CSS.escape(m.to));
      if (factory && canvas) {
        try { chart = factory(canvas); } catch (e) { console.error("insight chart", e); }
      }
    });

    // The source keeps its space in the layout but is hidden, so the page does
    // not reflow underneath the animation.
    card.classList.add("is-source");

    // Commit the starting geometry before changing it, so the transition has
    // two distinct values to animate between.  A forced reflow is used rather
    // than requestAnimationFrame: rAF is throttled to a crawl (or stopped
    // outright) in a backgrounded tab, which would strand the card at its
    // starting position with no way back.  Reading offsetHeight always works.
    var target = targetRect();
    void stage.offsetHeight;                              // flush layout
    backdrop.classList.add("on");
    stage.style.top = target.top + "px";
    stage.style.left = target.left + "px";
    stage.style.width = target.width + "px";
    stage.style.height = target.height + "px";
    if (chart) window.setTimeout(function () { chart.resize(); }, 460);

    function onKey(e) {
      if (e.key === "Escape") { close(); return; }
      if (e.key === "Enter" || e.key === " ") {
        if (e.target && e.target.hasAttribute && e.target.hasAttribute("data-zoom-flip")) return;
      }
    }
    document.addEventListener("keydown", onKey);

    open = { stage: stage, backdrop: backdrop, source: card, card: inner, chart: chart, onKey: onKey };

    backdrop.addEventListener("click", close);
    var firstBtn = inner.querySelector(".front [data-zoom-flip]");
    if (firstBtn) firstBtn.focus({ preventScroll: true });
  }

  function flip() {
    if (!open) return;
    open.card.classList.toggle("flipped");
    // Chart.js measures on construction, and a canvas that has been sitting
    // face-down carries a stale size until it is shown again.
    if (open.chart && !open.card.classList.contains("flipped")) {
      window.setTimeout(function () { open.chart.resize(); }, 260);
    }
  }

  // ---- delegated events; survives every HTMX swap of the KPI strip ---------
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-zoom-close]")) { close(); return; }
    if (e.target.closest("[data-zoom-flip]")) { flip(); return; }
    if (e.target.closest(".zoom-face")) return;          // interacting inside the modal
    var card = e.target.closest("[data-insight]");
    if (!card) return;
    // A KPI card may legitimately contain a link (e.g. an owner name); let the
    // link win rather than swallowing the navigation.
    if (e.target.closest("a")) return;
    e.preventDefault();
    openCard(card);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var card = e.target.closest && e.target.closest("[data-insight]");
    if (!card || open) return;
    e.preventDefault();
    openCard(card);
  });

  window.addEventListener("resize", function () {
    if (!open) return;
    var t = targetRect();
    open.stage.style.top = t.top + "px";
    open.stage.style.left = t.left + "px";
    open.stage.style.width = t.width + "px";
    open.stage.style.height = t.height + "px";
  });

  window.GTInsight = {
    /* A chart card registers how to rebuild itself at any size.  `factory` is
       called with a canvas element and must return the Chart instance. */
    chart: function (canvasId, factory) { FACTORIES[canvasId] = factory; },
    close: close,
  };

  // Keyboard reachability for every insight card, including ones HTMX swaps in.
  function markFocusable(root) {
    (root || document).querySelectorAll("[data-insight]:not([tabindex])").forEach(function (el) {
      el.setAttribute("tabindex", "0");
      el.setAttribute("role", "button");
    });
  }
  document.addEventListener("DOMContentLoaded", function () { markFocusable(); });
  document.body && markFocusable();
  document.addEventListener("htmx:afterSwap", function (e) { markFocusable(e.target); });
})();
