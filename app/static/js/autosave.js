/* Debounced autosave for the report form.
 *
 * The form holds ~25 fields that were previously only persisted on a successful
 * generate — a refresh, a stray back-click or a session hiccup lost all of it.
 * This saves a draft to analysis.form_json, the SAME field a successful generate
 * writes and that reopening the analysis pre-fills from, so a recovered draft
 * behaves exactly like a previously-run form.
 *
 * Delegated on `document`, so it survives the HTMX fragment swap.
 *
 * IMPORTANT: file inputs are skipped. Serialising them would re-upload the
 * workbook / pysam / datasheet PDFs on every keystroke.
 */
(function () {
  "use strict";

  var DEBOUNCE_MS = 1200;
  var timer = null;
  var inFlight = false;
  var pending = false;

  function form() {
    return document.getElementById("report-form");
  }

  function status(text, tone) {
    var el = document.getElementById("draft-status");
    if (!el) return;
    el.textContent = text || "";
    el.className = "draft-status" + (tone ? " draft-" + tone : "");
  }

  function save() {
    var f = form();
    if (!f) return;
    var url = f.getAttribute("data-draft-url");
    if (!url) return;
    if (inFlight) { pending = true; return; }

    // Text/select/textarea only — never the file inputs.
    var data = new FormData();
    var fields = f.querySelectorAll("input, select, textarea");
    for (var i = 0; i < fields.length; i++) {
      var el = fields[i];
      if (!el.name || el.type === "file" || el.disabled) continue;
      if ((el.type === "checkbox" || el.type === "radio") && !el.checked) continue;
      data.append(el.name, el.value);
    }

    inFlight = true;
    status("Saving…", "muted");
    fetch(url, { method: "POST", body: data, credentials: "same-origin" })
      .then(function (r) {
        status(r.ok ? "Draft saved" : "Draft not saved", r.ok ? "ok" : "warn");
      })
      .catch(function () { status("Draft not saved (offline?)", "warn"); })
      .finally(function () {
        inFlight = false;
        if (pending) { pending = false; schedule(); }
      });
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(save, DEBOUNCE_MS);
  }

  function onEdit(e) {
    var t = e.target;
    if (!t || !t.name || t.type === "file") return;
    if (!t.closest || !t.closest("#report-form")) return;
    status("Unsaved changes…", "muted");
    schedule();
  }

  document.addEventListener("input", onEdit);
  document.addEventListener("change", onEdit);

  // A successful generate persists the form server-side already — clear the note.
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.target && e.target.id === "report-preview") {
      clearTimeout(timer);
      status("");
    }
  });
})();
