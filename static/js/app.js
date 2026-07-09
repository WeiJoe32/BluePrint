/* ============================================================================
   BluePrint — frontend logic (plain vanilla JS, no build step)
   ----------------------------------------------------------------------------
   Three UI states toggled by showState(): "upload" -> "progress" -> "viewer".
   Talks to POST /api/generate, which streams newline-delimited JSON (NDJSON):
     {"type":"status","message":...}
     {"type":"heartbeat"}
     {"type":"progress","chars":N}
     {"type":"done","svg":"<svg ...>"}
     {"type":"error","message":...}
   ========================================================================== */

(function () {
  "use strict";

  var MAX_PHOTOS = 4;
  var MAX_EDGE_PX = 1568; // longest edge cap before downscaling for upload

  /* ── DOM refs ────────────────────────────────────────────────────────── */
  var wrapEl = document.getElementById("wrap");

  var stateUpload = document.getElementById("state-upload");
  var stateProgress = document.getElementById("state-progress");
  var stateViewer = document.getElementById("state-viewer");

  var dropzone = document.getElementById("dropzone");
  var photoInput = document.getElementById("photo-input");
  var photoError = document.getElementById("photo-error");
  var thumbGrid = document.getElementById("thumb-grid");

  var descriptionInput = document.getElementById("description");
  var dimensionLabelSelect = document.getElementById("dimension-label");
  var dimensionCustomLabel = document.getElementById("dimension-custom-label");
  var dimensionValueInput = document.getElementById("dimension-value");
  var dimensionUnitSelect = document.getElementById("dimension-unit");
  var generateBtn = document.getElementById("generate-btn");

  var progressStatus = document.getElementById("progress-status");
  var progressElapsed = document.getElementById("progress-elapsed");
  var progressCharsWrap = document.getElementById("progress-chars-wrap");
  var progressChars = document.getElementById("progress-chars");
  var cancelBtn = document.getElementById("cancel-btn");

  var viewerPane = document.getElementById("viewer-pane");
  var drawingImg = document.getElementById("drawing-img");
  var zoomOutBtn = document.getElementById("zoom-out-btn");
  var zoomInBtn = document.getElementById("zoom-in-btn");
  var zoomResetBtn = document.getElementById("zoom-reset-btn");
  var zoomPctLabel = document.getElementById("zoom-pct");
  var printBtn = document.getElementById("print-btn");
  var downloadBtn = document.getElementById("download-btn");
  var newDrawingBtn = document.getElementById("new-drawing-btn");

  /* ── State ───────────────────────────────────────────────────────────── */
  var selectedPhotos = []; // [{file, objectUrl}]
  var elapsedTimer = null;
  var elapsedSeconds = 0;
  var abortController = null;
  var currentBlobUrl = null; // the SVG blob URL currently shown in the viewer
  var zoomLevel = 1; // 0.25 - 4, applied as img width %

  /* ── State switching ─────────────────────────────────────────────────── */
  function showState(name) {
    stateUpload.classList.toggle("hidden", name !== "upload");
    stateProgress.classList.toggle("hidden", name !== "progress");
    stateViewer.classList.toggle("hidden", name !== "viewer");
    wrapEl.classList.toggle("wide", name === "viewer");
  }

  /* ── Photo downscale + encode ───────────────────────────────────────── */
  // Shrinks the image so its longest edge is at most MAX_EDGE_PX, re-encodes
  // as JPEG, and returns the bare base64 payload (no "data:image/jpeg;..." prefix)
  // because the server expects raw base64 in the JSON body.
  async function downscaleToJpegBase64(file) {
    var bitmap = await createImageBitmap(file);
    var width = bitmap.width;
    var height = bitmap.height;
    var longestEdge = Math.max(width, height);

    if (longestEdge > MAX_EDGE_PX) {
      var scale = MAX_EDGE_PX / longestEdge;
      width = Math.round(width * scale);
      height = Math.round(height * scale);
    }

    var canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    var ctx = canvas.getContext("2d");
    ctx.drawImage(bitmap, 0, 0, width, height);
    bitmap.close();

    var dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    return dataUrl.replace(/^data:image\/jpeg;base64,/, "");
  }

  /* ── Photo selection / thumbnails ───────────────────────────────────── */
  function photoKey(file) {
    return file.name + ":" + file.size;
  }

  function addFiles(fileList) {
    var files = Array.prototype.slice.call(fileList);
    var existingKeys = selectedPhotos.map(function (p) { return photoKey(p.file); });
    var rejectedForCount = false;

    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      if (!file.type || file.type.indexOf("image/") !== 0) continue;
      var key = photoKey(file);
      if (existingKeys.indexOf(key) !== -1) continue; // dedupe by name+size
      if (selectedPhotos.length >= MAX_PHOTOS) {
        rejectedForCount = true;
        break;
      }
      var objectUrl = URL.createObjectURL(file);
      selectedPhotos.push({ file: file, objectUrl: objectUrl });
      existingKeys.push(key);
    }

    if (rejectedForCount) {
      showPhotoError("Only " + MAX_PHOTOS + " photos allowed — extra photos were not added.");
    } else {
      clearPhotoError();
    }

    renderThumbnails();
    updateGenerateEnabled();
  }

  function removePhoto(index) {
    var removed = selectedPhotos.splice(index, 1)[0];
    if (removed) URL.revokeObjectURL(removed.objectUrl);
    clearPhotoError();
    renderThumbnails();
    updateGenerateEnabled();
  }

  function showPhotoError(message) {
    photoError.textContent = message;
    photoError.classList.remove("hidden");
  }

  function clearPhotoError() {
    photoError.textContent = "";
    photoError.classList.add("hidden");
  }

  function renderThumbnails() {
    thumbGrid.innerHTML = "";
    if (selectedPhotos.length === 0) {
      thumbGrid.classList.add("hidden");
      return;
    }
    thumbGrid.classList.remove("hidden");

    selectedPhotos.forEach(function (photo, index) {
      var thumb = document.createElement("div");
      thumb.className = "thumb";

      var img = document.createElement("img");
      img.src = photo.objectUrl;
      img.alt = photo.file.name;
      thumb.appendChild(img);

      var removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "thumb-remove";
      removeBtn.setAttribute("aria-label", "Remove photo");
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", function () {
        removePhoto(index);
      });
      thumb.appendChild(removeBtn);

      thumbGrid.appendChild(thumb);
    });
  }

  function revokeAllPhotoUrls() {
    selectedPhotos.forEach(function (p) { URL.revokeObjectURL(p.objectUrl); });
    selectedPhotos = [];
  }

  dropzone.addEventListener("click", function () { photoInput.click(); });
  photoInput.addEventListener("change", function () {
    addFiles(photoInput.files);
    photoInput.value = ""; // allow re-selecting the same file later
  });

  // Basic drag-and-drop onto the dropzone button.
  ["dragenter", "dragover"].forEach(function (evtName) {
    dropzone.addEventListener(evtName, function (e) {
      e.preventDefault();
      dropzone.classList.add("is-drag");
    });
  });
  ["dragleave", "drop"].forEach(function (evtName) {
    dropzone.addEventListener(evtName, function (e) {
      e.preventDefault();
      dropzone.classList.remove("is-drag");
    });
  });
  dropzone.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
  });

  /* ── Dimension inputs ────────────────────────────────────────────────── */
  dimensionLabelSelect.addEventListener("change", function () {
    var isCustom = dimensionLabelSelect.value === "custom";
    dimensionCustomLabel.classList.toggle("hidden", !isCustom);
    updateGenerateEnabled();
  });

  function getDimensionLabel() {
    if (dimensionLabelSelect.value === "custom") {
      return (dimensionCustomLabel.value || "").trim();
    }
    return dimensionLabelSelect.value;
  }

  function getDimensionValue() {
    var value = parseFloat(dimensionValueInput.value);
    return isNaN(value) ? null : value;
  }

  function isFormValid() {
    if (selectedPhotos.length < 1 || selectedPhotos.length > MAX_PHOTOS) return false;
    var label = getDimensionLabel();
    if (!label) return false;
    var value = getDimensionValue();
    if (value === null || value < 0.1) return false;
    return true;
  }

  function updateGenerateEnabled() {
    generateBtn.disabled = !isFormValid();
  }

  [dimensionValueInput, dimensionCustomLabel, descriptionInput].forEach(function (el) {
    el.addEventListener("input", updateGenerateEnabled);
  });

  /* ── Elapsed timer ───────────────────────────────────────────────────── */
  function startElapsedTimer() {
    elapsedSeconds = 0;
    progressElapsed.textContent = "0s";
    elapsedTimer = setInterval(function () {
      elapsedSeconds += 1;
      progressElapsed.textContent = elapsedSeconds + "s";
    }, 1000);
  }

  function stopElapsedTimer() {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  /* ── Generate ────────────────────────────────────────────────────────── */
  generateBtn.addEventListener("click", startGenerate);
  cancelBtn.addEventListener("click", function () {
    if (abortController) abortController.abort();
  });

  function resetProgressUi() {
    progressStatus.textContent = "Analyzing photos…";
    progressCharsWrap.classList.add("hidden");
    progressChars.textContent = "0";
  }

  async function startGenerate() {
    if (!isFormValid()) return;

    revokeStaleBlobUrl();
    resetProgressUi();
    showState("progress");
    startElapsedTimer();

    abortController = new AbortController();

    try {
      var images = await Promise.all(
        selectedPhotos.map(function (p) { return downscaleToJpegBase64(p.file); })
      );

      var payload = {
        images: images,
        dimension_label: getDimensionLabel(),
        dimension_value: getDimensionValue(),
        unit: dimensionUnitSelect.value,
        description: (descriptionInput.value || "").trim(),
        quality: getSelectedQuality()
      };

      var response = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: abortController.signal
      });

      if (!response.ok) {
        var errMessage = "Something went wrong. Please try again.";
        try {
          var errBody = await response.json();
          if (errBody && errBody.error) errMessage = errBody.error;
        } catch (parseErr) {
          // response body wasn't JSON — fall back to the generic message
        }
        failGenerate(errMessage);
        return;
      }

      await consumeStream(response);
    } catch (err) {
      if (err && err.name === "AbortError") {
        // user cancelled — quietly return to the upload screen
        stopElapsedTimer();
        showState("upload");
        return;
      }
      failGenerate("Network error — check your connection and try again.");
    }
  }

  function getSelectedQuality() {
    var checked = document.querySelector('input[name="quality"]:checked');
    return checked ? checked.value : "fast";
  }

  async function consumeStream(response) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";

    while (true) {
      var result = await reader.read();
      if (result.done) break;

      buffer += decoder.decode(result.value, { stream: true });
      var lines = buffer.split("\n");
      buffer = lines.pop(); // keep the trailing partial line for the next chunk

      for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line) continue;
        handleStreamEvent(JSON.parse(line));
      }
    }

    // flush any final complete line left without a trailing newline
    var tail = buffer.trim();
    if (tail) handleStreamEvent(JSON.parse(tail));
  }

  function handleStreamEvent(event) {
    switch (event.type) {
      case "status":
        progressStatus.textContent = event.message;
        break;
      case "heartbeat":
        // keep-alive only — the elapsed timer already ticks independently
        break;
      case "progress":
        progressCharsWrap.classList.remove("hidden");
        progressChars.textContent = event.chars;
        break;
      case "done":
        completeGenerate(event.svg);
        break;
      case "error":
        failGenerate(event.message || "Generation failed.");
        break;
      default:
        break;
    }
  }

  function completeGenerate(svgText) {
    stopElapsedTimer();
    revokeStaleBlobUrl();

    var blob = new Blob([svgText], { type: "image/svg+xml" });
    currentBlobUrl = URL.createObjectURL(blob);
    drawingImg.src = currentBlobUrl;

    resetZoom();
    showState("viewer");
  }

  function failGenerate(message) {
    stopElapsedTimer();
    showState("upload");
    showPhotoError(message);
  }

  function revokeStaleBlobUrl() {
    if (currentBlobUrl) {
      URL.revokeObjectURL(currentBlobUrl);
      currentBlobUrl = null;
    }
  }

  /* ── Viewer: zoom / print / download / new drawing ──────────────────── */
  var ZOOM_STEP = 1.25;
  var ZOOM_MIN = 0.25;
  var ZOOM_MAX = 4;

  function applyZoom() {
    drawingImg.style.width = (zoomLevel * 100) + "%";
    zoomPctLabel.textContent = Math.round(zoomLevel * 100) + "%";
  }

  function resetZoom() {
    zoomLevel = 1; // fit width
    applyZoom();
  }

  function zoomIn() {
    zoomLevel = Math.min(ZOOM_MAX, zoomLevel * ZOOM_STEP);
    applyZoom();
  }

  function zoomOut() {
    zoomLevel = Math.max(ZOOM_MIN, zoomLevel / ZOOM_STEP);
    applyZoom();
  }

  zoomInBtn.addEventListener("click", zoomIn);
  zoomOutBtn.addEventListener("click", zoomOut);
  zoomResetBtn.addEventListener("click", resetZoom);

  // Ctrl/Cmd + wheel zooms the drawing instead of scrolling the pane.
  viewerPane.addEventListener("wheel", function (e) {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    if (e.deltaY < 0) zoomIn(); else zoomOut();
  }, { passive: false });

  printBtn.addEventListener("click", function () { window.print(); });

  downloadBtn.addEventListener("click", function () {
    if (!currentBlobUrl) return;
    var link = document.createElement("a");
    link.href = currentBlobUrl;
    link.download = "blueprint.svg";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  });

  newDrawingBtn.addEventListener("click", function () {
    revokeStaleBlobUrl();
    drawingImg.src = "";
    resetForm();
    showState("upload");
  });

  // Full reset of the upload form — used when starting a fresh drawing so
  // nothing from the previous run (photos, description, dimension) lingers.
  function resetForm() {
    revokeAllPhotoUrls();
    renderThumbnails();
    clearPhotoError();
    descriptionInput.value = "";
    dimensionLabelSelect.value = "height";
    dimensionCustomLabel.value = "";
    dimensionCustomLabel.classList.add("hidden");
    dimensionValueInput.value = "";
    dimensionUnitSelect.value = "mm";
    var fastRadio = document.querySelector('input[name="quality"][value="fast"]');
    if (fastRadio) fastRadio.checked = true;
    updateGenerateEnabled();
  }

  /* ── Init ────────────────────────────────────────────────────────────── */
  updateGenerateEnabled();
})();
