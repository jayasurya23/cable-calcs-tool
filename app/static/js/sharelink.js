/* Shareable links: copy-to-clipboard buttons, and making a linked-to revision
 * actually visible when someone opens the link you sent them.
 *
 * Delegated on `document`, like the app's other scripts, so it survives HTMX
 * swaps with no re-init.
 *
 * Markup contract:
 *   <button data-copy-link="/sam/analysis/12">Copy link</button>
 *      -> copies the ABSOLUTE url (origin + path) so it can be pasted into chat
 *   <tr id="rev-7-3">                          (analysis 7, revision 3)
 *      -> /projects/4#rev-7-3 opens the row's <details> and highlights it
 */
(function () {
  "use strict";

  function absolute(path) {
    try {
      return new URL(path, window.location.origin).href;
    } catch (e) {
      return window.location.origin + path;
    }
  }

  // navigator.clipboard needs a secure context (https, or localhost). Fall back
  // to a hidden textarea so the button is never simply dead.
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy") ? resolve() : reject(new Error("copy refused"));
      } catch (err) {
        reject(err);
      } finally {
        document.body.removeChild(ta);
      }
    });
  }

  function flash(btn, text, ok) {
    var original = btn.getAttribute("data-label") || btn.textContent;
    btn.setAttribute("data-label", original);
    btn.textContent = text;
    btn.classList.toggle("copied", !!ok);
    window.setTimeout(function () {
      btn.textContent = btn.getAttribute("data-label") || original;
      btn.classList.remove("copied");
    }, 1600);
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-copy-link]");
    if (!btn) return;
    e.preventDefault();
    var url = absolute(btn.getAttribute("data-copy-link"));
    copy(url).then(
      function () { flash(btn, "Link copied", true); },
      function () {
        // Couldn't reach the clipboard — show the URL so it can be copied by hand
        // rather than leaving the engineer with a button that did nothing.
        flash(btn, "Press Ctrl+C", false);
        window.prompt("Copy this link:", url);
      }
    );
  });

  /* A revision permalink lands on #rev-<analysisId>-<revNumber>. That row lives
   * inside a collapsed <details>, which the browser will not open on its own, so
   * the recipient would land on a page showing nothing they were sent. */
  function revealHash() {
    var hash = window.location.hash;
    if (!hash || hash.length < 2) return;
    var target;
    try {
      target = document.querySelector(hash);
    } catch (err) {
      return;                                  // not a usable selector
    }
    if (!target) return;

    var node = target.parentElement;
    while (node) {
      if (node.tagName === "DETAILS") node.open = true;
      node = node.parentElement;
    }
    document.querySelectorAll(".linked-target").forEach(function (el) {
      el.classList.remove("linked-target");
    });
    target.classList.add("linked-target");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  document.addEventListener("DOMContentLoaded", revealHash);
  window.addEventListener("hashchange", revealHash);
  document.addEventListener("htmx:afterSwap", function () {
    if (window.location.hash) revealHash();
  });
})();
