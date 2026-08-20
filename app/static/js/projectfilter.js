/* Client-side search / status-filter / sort for the project list, plus local-time
 * rendering of UTC timestamps.
 *
 * Every project is already rendered on the page, so filtering here is instant and
 * needs no round-trip. Delegated on `document` for consistency with the app's
 * other scripts; it no-ops on pages without the toolbar.
 */
(function () {
  "use strict";

  function cards() {
    var host = document.getElementById("project-cards");
    return host ? Array.prototype.slice.call(host.querySelectorAll(".project-card")) : [];
  }

  function state() {
    var seg = document.querySelector(".seg-btn.seg-active");
    var search = document.getElementById("project-search");
    var sort = document.getElementById("project-sort");
    return {
      status: seg ? seg.getAttribute("data-status") : "active",
      q: (search ? search.value : "").trim().toLowerCase(),
      sort: sort ? sort.value : "recent",
    };
  }

  function apply() {
    var host = document.getElementById("project-cards");
    if (!host) return;
    var s = state();
    var list = cards();
    var shown = 0;

    list.forEach(function (c) {
      var okStatus = s.status === "all" || c.getAttribute("data-status") === s.status;
      var hay = c.getAttribute("data-name") + " " + c.getAttribute("data-code");
      var okText = !s.q || hay.indexOf(s.q) !== -1;
      var on = okStatus && okText;
      c.hidden = !on;
      if (on) shown++;
    });

    var key = { recent: "data-activity", created: "data-created", name: "data-name" }[s.sort];
    var sorted = list.slice().sort(function (a, b) {
      var av = a.getAttribute(key) || "";
      var bv = b.getAttribute(key) || "";
      return s.sort === "name" ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    sorted.forEach(function (c) { host.appendChild(c); });

    var count = document.getElementById("project-count");
    if (count) {
      count.textContent = shown + " of " + list.length +
        (s.status === "all" ? "" : " " + s.status);
    }
    var empty = document.getElementById("project-empty");
    if (empty) empty.hidden = shown !== 0 || list.length === 0;
  }

  document.addEventListener("input", function (e) {
    if (e.target && e.target.id === "project-search") apply();
  });
  document.addEventListener("change", function (e) {
    if (e.target && e.target.id === "project-sort") apply();
  });
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".seg-btn");
    if (btn) {
      e.preventDefault();
      var group = btn.parentNode;
      group.querySelectorAll(".seg-btn").forEach(function (b) {
        b.classList.toggle("seg-active", b === btn);
      });
      apply();
      return;
    }
    // "+ New project" / "+ New client": <button id="new-X-toggle"> reveals
    // <form id="new-X-form">.
    var toggle = e.target.closest && e.target.closest('[id$="-toggle"]');
    if (toggle) {
      var form = document.getElementById(toggle.id.replace(/-toggle$/, "-form"));
      if (form) {
        form.hidden = !form.hidden;
        if (!form.hidden) {
          var first = form.querySelector("input");
          if (first) first.focus();
        }
      }
    }
  });

  /* Moving a project: only the chosen client's portfolios may be picked. The
   * server re-checks this — a page left open while someone else adds or moves a
   * portfolio could otherwise post a portfolio belonging to another client. */
  function syncPortfolios() {
    var client = document.getElementById("move-client");
    var portfolio = document.getElementById("move-portfolio");
    if (!client || !portfolio) return;
    var chosen = client.value;
    var stillValid = false;
    Array.prototype.forEach.call(portfolio.options, function (opt) {
      if (!opt.value) return;                       // the "none" option always stays
      var mine = opt.getAttribute("data-client") === chosen;
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (mine && opt.selected) stillValid = true;
    });
    if (!stillValid) portfolio.value = "";          // don't carry a foreign portfolio over
  }

  document.addEventListener("change", function (e) {
    if (e.target && e.target.id === "move-client") syncPortfolios();
  });
  document.addEventListener("DOMContentLoaded", syncPortfolios);

  // Timestamps are stored and rendered in UTC; show them in the viewer's zone.
  function localize() {
    document.querySelectorAll("time.js-localdate").forEach(function (el) {
      var iso = el.getAttribute("datetime");
      if (!iso) return;
      var d = new Date(iso);
      if (!isNaN(d.getTime())) {
        el.textContent = d.toLocaleDateString();
        el.title = d.toLocaleString();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () { localize(); apply(); });
  document.addEventListener("htmx:afterSwap", localize);
})();
