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

  // Click the zone -> open the file browser (but not when the click IS the picker).
  document.addEventListener("click", function (e) {
    var dz = e.target.closest && e.target.closest(".js-dropzone");
    if (!dz) return;
    if (e.target.id === "mod-file-picker") return;
    var r = refs(dz);
    if (r.picker) r.picker.click();
  });

  document.addEventListener("change", function (e) {
    if (e.target && e.target.id === "mod-file-picker") {
      var dz = e.target.closest(".js-dropzone");
      if (dz) handle(dz, e.target.files);
      e.target.value = ""; // allow re-picking the same file later
    }
  });

  // Prevent the browser from navigating to a dropped file anywhere on the page;
  // only the dropzone routes them.
  document.addEventListener("dragover", function (e) {
    e.preventDefault();
    var dz = e.target.closest && e.target.closest(".js-dropzone");
    if (dz) dz.classList.add("dragover");
  });
  document.addEventListener("dragleave", function (e) {
    var dz = e.target.closest && e.target.closest(".js-dropzone");
    if (dz && !dz.contains(e.relatedTarget)) dz.classList.remove("dragover");
  });
  document.addEventListener("drop", function (e) {
    e.preventDefault();
    var dz = e.target.closest && e.target.closest(".js-dropzone");
    if (!dz) return;
    dz.classList.remove("dragover");
    handle(dz, e.dataTransfer.files);
  });

  // Enter/Space on the focused zone opens the browser (keyboard accessibility).
  document.addEventListener("keydown", function (e) {
    var dz = e.target.closest && e.target.closest(".js-dropzone");
    if (!dz) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      var r = refs(dz);
      if (r.picker) r.picker.click();
    }
  });
})();
