/* Drag-and-drop for the "Add module" control.
 *
 * Pure event delegation on `document`, so it keeps working after HTMX swaps the
 * modules section (no per-element init needed). A dropzone (`.js-dropzone`) and
 * its two hidden file inputs live inside a `.module-add` container:
 *   #new-module-file   -> the Excel workbook  (.xlsx / .xlsm)
 *   #new-module-pysam  -> the optional pysam   (.json)
 * Dropped/browsed files are routed by extension into those inputs (which HTMX
 * then submits), a preview is shown, and the "Add module" button enables once a
 * workbook is present.
 */
(function () {
  "use strict";

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function refs(dz) {
    var box = dz.closest(".module-add") || document;
    return {
      picker: box.querySelector("#mod-file-picker"),
      wb: box.querySelector("#new-module-file"),
      ps: box.querySelector("#new-module-pysam"),
      list: box.querySelector("#dz-files"),
      btn: box.querySelector("#add-module-btn"),
      note: box.querySelector("#dz-note"),
    };
  }

  function setFile(input, file) {
    var dt = new DataTransfer();
    if (file) dt.items.add(file);
    input.files = dt.files;
  }

  function render(r) {
    if (!r.wb || !r.ps) return;
    var wb = r.wb.files[0];
    var ps = r.ps.files[0];
    var html = "";
    if (wb) html += '<span class="dz-chip dz-xlsx">📊 ' + esc(wb.name) + "</span>";
    if (ps) html += '<span class="dz-chip dz-json">📄 ' + esc(ps.name) + " <em>pysam</em></span>";
    if (r.list) {
      r.list.innerHTML = html;
      r.list.hidden = !html;
    }
    if (r.btn) r.btn.disabled = !wb;
    if (r.note) {
      if (wb && !ps) {
        r.note.textContent = "No pysam — this module’s System Size / DC-AC will show a dash. Add its pysam to fill them.";
        r.note.hidden = false;
      } else {
        r.note.hidden = true;
      }
    }
  }

  function handle(dz, fileList) {
    var r = refs(dz);
    var unknown = [];
    for (var i = 0; i < fileList.length; i++) {
      var f = fileList[i];
      var n = f.name.toLowerCase();
      if (n.endsWith(".xlsx") || n.endsWith(".xlsm")) setFile(r.wb, f);
      else if (n.endsWith(".json")) setFile(r.ps, f);
      else unknown.push(f.name);
    }
    render(r);
    if (unknown.length && r.note) {
      r.note.textContent = "Skipped (need .xlsx or .json): " + unknown.join(", ");
      r.note.hidden = false;
    }
  }

  var SEL = ".js-dropzone, .js-ds-dropzone";

  // The file input inside a zone: module zone -> #mod-file-picker (routed by JS);
  // datasheet zone -> #ds-file-picker (which auto-posts itself via HTMX).
  function zonePicker(dz) {
    return dz.querySelector('input[type="file"]');
  }

  // Feed files to a zone. Datasheet zone: set them on its own picker and fire a
  // change event so HTMX auto-uploads (the input is the request element). Module
  // zone: route by extension into the workbook / pysam inputs and preview.
  function feed(dz, fileList) {
    if (dz.classList.contains("js-ds-dropzone")) {
      var picker = zonePicker(dz);
      if (!picker) return;
      var dt = new DataTransfer();
      for (var i = 0; i < fileList.length; i++) dt.items.add(fileList[i]);
      picker.files = dt.files;
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      handle(dz, fileList);
    }
  }

  document.addEventListener("click", function (e) {
    var dz = e.target.closest && e.target.closest(SEL);
    if (!dz) return;
    if (e.target.matches && e.target.matches('input[type="file"]')) return;
    var picker = zonePicker(dz);
    if (picker) picker.click();
  });

  document.addEventListener("change", function (e) {
    if (e.target && e.target.id === "mod-file-picker") {
      var dz = e.target.closest(".js-dropzone");
      if (dz) handle(dz, e.target.files);
      e.target.value = ""; // allow re-picking the same file later
    }
  });

  // Prevent the browser from navigating to a dropped file anywhere on the page;
  // only the dropzones route them.
  document.addEventListener("dragover", function (e) {
    e.preventDefault();
    var dz = e.target.closest && e.target.closest(SEL);
    if (dz) dz.classList.add("dragover");
  });
  document.addEventListener("dragleave", function (e) {
    var dz = e.target.closest && e.target.closest(SEL);
    if (dz && !dz.contains(e.relatedTarget)) dz.classList.remove("dragover");
  });
  document.addEventListener("drop", function (e) {
    e.preventDefault();
    var dz = e.target.closest && e.target.closest(SEL);
    if (!dz) return;
    dz.classList.remove("dragover");
    feed(dz, e.dataTransfer.files);
  });

  // Enter/Space on the focused zone opens the browser (keyboard accessibility).
  document.addEventListener("keydown", function (e) {
    var dz = e.target.closest && e.target.closest(SEL);
    if (!dz) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      var picker = zonePicker(dz);
      if (picker) picker.click();
    }
  });

  /* ── New-analysis page: repeatable module rows ──────────────────────────
   * Row 1 uses the original field names (workbook / pysam) so the upload route
   * keeps its typed signature. Rows 2+ are module_wb_N / module_ps_N /
   * module_label_N and are read out of the raw form server-side. Extra rows are
   * NOT `required` — an empty row is skipped rather than blocking the submit.
   */
  var nextRow = 2;

  function renumber() {
    var rows = document.querySelectorAll("#module-rows .mod-row");
    rows.forEach(function (row, i) {
      var head = row.querySelector(".mod-row-head b");
      if (head) head.textContent = "Module " + (i + 1);
    });
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("#add-module-row")) {
      var host = document.getElementById("module-rows");
      if (!host) return;
      var n = nextRow++;
      var row = document.createElement("div");
      row.className = "mod-row";
      row.setAttribute("data-mod-row", String(n));
      row.innerHTML =
        '<div class="mod-row-head"><b>Module ' + n + '</b>' +
        '<button type="button" class="btn-remove" data-remove-mod-row title="Remove this module">&times;</button></div>' +
        '<div class="mod-row-fields">' +
        '<label class="mod-f">Workbook <input type="file" name="module_wb_' + n + '" accept=".xlsx,.xlsm"></label>' +
        '<label class="mod-f">pysam JSON <span class="opt">(optional)</span>' +
        '<input type="file" name="module_ps_' + n + '" accept=".json"></label>' +
        '<label class="mod-f">Label <span class="opt">(optional)</span>' +
        '<input type="text" name="mod_label_' + n + '" placeholder="e.g. 615 W Module"></label>' +
        "</div>";
      host.appendChild(row);
      renumber();
      var f = row.querySelector('input[type="file"]');
      if (f) f.focus();
      return;
    }
    var rm = e.target.closest && e.target.closest("[data-remove-mod-row]");
    if (rm) {
      var r = rm.closest(".mod-row");
      if (r) { r.remove(); renumber(); }
    }
  });
})();
