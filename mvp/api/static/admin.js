/* Small helpers for the admin dashboard.
   Every state-changing call carries BOTH the X-Requested-With header and the
   session's CSRF token, so a cross-site page cannot forge it. */
(function () {
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)gt_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  window.gt = {
    async api(method, url, body) {
      const res = await fetch(url, {
        method: method,
        credentials: "same-origin",
        headers: Object.assign(
          { "X-Requested-With": "GauTrack", "X-CSRF-Token": csrf() },
          body ? { "Content-Type": "application/json" } : {}
        ),
        body: body ? JSON.stringify(body) : undefined,
      });
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!res.ok) throw new Error((data && (data.detail && data.detail.reason || data.detail)) || res.statusText);
      return data;
    },
    fmtInt(n) { return (n === null || n === undefined) ? "-" : Number(n).toLocaleString("en-IN"); },
    fmtRs(n) { return "₹" + Number(n || 0).toLocaleString("en-IN"); },
  };
})();
