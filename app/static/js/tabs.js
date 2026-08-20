/* Tabbed navigation for the analysis page.
 *
 * Pure event delegation on `document`, so it keeps working after HTMX swaps the
 * whole report fragment in (POST /sam/upload -> #report) with no re-init.
 *
 * Markup contract:
 *   <div class="js-tabs">
 *     <button data-tab-target="results">…</button>      (in the tab bar)
 *     <div data-tab-panel="results">…</div>             (the panel)
 *
 * Panels are shown/hidden with the `hidden` attribute — NOT removed from the DOM —
 * so every form field inside an inactive tab still submits with the single
 * <form> that spans the "details" tab. (Nothing in that form is `required`, so
 * hiding never traps browser validation on a non-focusable control.)
 */
(function () {
  "use strict";

  function tabRoot(el) {
    return el.closest(".js-tabs");
  }

  function activate(root, name, opts) {
    if (!root || !name) return;
    var scrollTop = !(opts && opts.noScroll);

    root.querySelectorAll("[data-tab-panel]").forEach(function (p) {
      if (tabRoot(p) !== root) return;             // ignore nested tab groups
      p.hidden = p.getAttribute("data-tab-panel") !== name;
    });

    root.querySelectorAll("[data-tab-target]").forEach(function (b) {
      if (tabRoot(b) !== root) return;
      var on = b.getAttribute("data-tab-target") === name;
      b.classList.toggle("tab-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });

    root.setAttribute("data-active-tab", name);
    if (scrollTop) {
      var bar = root.querySelector(".tabbar");
      if (bar && bar.getBoundingClientRect().top < 0) {
        bar.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }

  // Expose so other snippets (e.g. a "go to details" link) can drive it.
  window.activateTab = function (name) {
    var root = document.querySelector(".js-tabs");
    activate(root, name);
  };

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-tab-target]");
    if (!btn || btn.disabled) return;
    e.preventDefault();
    activate(tabRoot(btn), btn.getAttribute("data-tab-target"));
  });

  // A control that jumps to a tab without BEING one (e.g. an empty-state
  // "Add a module" button). Kept separate from [data-tab-target] so it is not
  // swept into the tablist's active-class / roving-tabindex bookkeeping, which
  // would otherwise leave it unreachable by keyboard.
  document.addEventListener("click", function (e) {
    var go = e.target.closest && e.target.closest("[data-goto-tab]");
    if (!go || go.disabled) return;
    e.preventDefault();
    activate(tabRoot(go) || document.querySelector(".js-tabs"),
             go.getAttribute("data-goto-tab"));
  });

  // Left/Right arrows move between tabs (standard tablist behaviour).
  document.addEventListener("keydown", function (e) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    var btn = e.target.closest && e.target.closest("[data-tab-target]");
    if (!btn) return;
    var root = tabRoot(btn);
    var tabs = Array.prototype.filter.call(
      root.querySelectorAll("[data-tab-target]"),
      function (b) { return tabRoot(b) === root && !b.disabled; }
    );
    var i = tabs.indexOf(btn);
    if (i < 0) return;
    var next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    e.preventDefault();
    next.focus();
    activate(root, next.getAttribute("data-tab-target"));
  });

  // When a report finishes generating, HTMX swaps the preview in — surface it.
  document.addEventListener("htmx:afterSwap", function (e) {
    var t = e.target;
    if (!t || t.id !== "report-preview") return;
    var root = tabRoot(t);
    if (!root) return;
    var tab = root.querySelector('[data-tab-target="preview"]');
    if (tab) tab.disabled = false;                 // it's real now
    root.classList.add("has-preview");
    activate(root, "preview");
  });
})();
