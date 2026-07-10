/* ============================================================================
   BluePrint — frontend logic (plain vanilla JS, no build step)
   ----------------------------------------------------------------------------
   Four UI states toggled by showState(): "upload" -> "progress" -> "viewer",
   plus "gallery" (saved drawings), reachable from anywhere via the header
   button.

   Talks to:
     POST /api/detect-scale  { image }
       -> { found, px_per_mm, marker_size_mm, annotated_image, angle_warning }
       -> { found: false }
     POST /api/generate      { images, dimensions: [{label, value}, ...1-6],
                                unit, description, quality, scale? }
     POST /api/refine        { images, dimensions, unit, description, scale,
                                quality, turns, instruction }
     POST /api/review        { images, dimensions, unit, description, scale,
                                quality, svg }
     POST /api/export-dxf    { svg } -> binary DXF download

   /api/generate, /api/refine and /api/review all stream newline-delimited
   JSON (NDJSON):
     {"type":"status","message":...}
     {"type":"heartbeat"}
     {"type":"progress","chars":N}
     {"type":"done","svg":"<svg ...>"}                          (generate/refine)
     {"type":"done","svg":"<svg ...>"|null,"defects":["...",...]} (review)
     {"type":"error","message":...}
   The same stream reader (consumeStream) drives all three — a "mode" flag
   decides which progress UI (the full-screen progress state, or the inline
   refine/review strip inside the viewer) gets updated as events arrive.
   ========================================================================== */

(function () {
  "use strict";

  var MAX_PHOTOS = 4;
  var MAX_EDGE_PX = 2576; // high-res tier limit for Sonnet 5 / Opus 4.8 (was 1568)
  var MAX_REFINE_TURNS = 8;
  var MAX_DIMENSIONS = 6;
  var GALLERY_KEY = "blueprint_gallery";
  var DIMENSION_LABELS = [
    { value: "height", text: "Height" },
    { value: "width", text: "Width" },
    { value: "depth", text: "Depth" },
    { value: "diameter", text: "Diameter" },
    { value: "length", text: "Length" },
    { value: "hole_spacing", text: "Hole spacing" },
    { value: "custom", text: "Custom…" }
  ];

  /* ── DOM refs ────────────────────────────────────────────────────────── */
  var wrapEl = document.getElementById("wrap");

  var stateUpload = document.getElementById("state-upload");
  var stateProgress = document.getElementById("state-progress");
  var stateViewer = document.getElementById("state-viewer");
  var stateGallery = document.getElementById("state-gallery");

  var dropzone = document.getElementById("dropzone");
  var photoInput = document.getElementById("photo-input");
  var photoError = document.getElementById("photo-error");
  var thumbGrid = document.getElementById("thumb-grid");

  var descriptionInput = document.getElementById("description");
  var dimensionRowsEl = document.getElementById("dimension-rows");
  var addDimensionBtn = document.getElementById("add-dimension-btn");
  var dimensionUnitSelect = document.getElementById("dimension-unit");
  var generateBtn = document.getElementById("generate-btn");
  var qualityPills = document.querySelectorAll(".pill");

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
  var dxfBtn = document.getElementById("dxf-btn");
  var newDrawingBtn = document.getElementById("new-drawing-btn");

  var refineInput = document.getElementById("refine-input");
  var refineSendBtn = document.getElementById("refine-send-btn");
  var refineTurnLabel = document.getElementById("refine-turn-label");
  var refineControls = document.getElementById("refine-controls");
  var refineGalleryNote = document.getElementById("refine-gallery-note");
  var refineProgress = document.getElementById("refine-progress");
  var refineProgressStatus = document.getElementById("refine-progress-status");

  var reviewRow = document.getElementById("review-row");
  var reviewBtn = document.getElementById("review-btn");
  var reviewPanel = document.getElementById("review-panel");
  var reviewDefectsEl = document.getElementById("review-defects");
  var reviewThumbBefore = document.getElementById("review-thumb-before");
  var reviewThumbAfter = document.getElementById("review-thumb-after");
  var reviewAcceptBtn = document.getElementById("review-accept-btn");
  var reviewKeepBtn = document.getElementById("review-keep-btn");
  var reviewCloseBtn = document.getElementById("review-close-btn");

  var galleryBtn = document.getElementById("gallery-btn");
  var galleryGrid = document.getElementById("gallery-grid");
  var galleryEmpty = document.getElementById("gallery-empty");
  var galleryBackBtn = document.getElementById("gallery-back-btn");

  var toastEl = document.getElementById("toast");

  /* ── State ───────────────────────────────────────────────────────────── */
  var selectedPhotos = []; // [{file, objectUrl, b64, scaleState, pxPerMm, markerSizeMm}]
  var scaleSourcePhoto = null; // the first photo whose marker set the session scale
  var scaleQueue = [];
  var scaleQueueBusy = false;

  var elapsedTimer = null;
  var elapsedSeconds = 0;
  var abortController = null; // generate
  var refineAbortController = null;
  var currentBlobUrl = null; // the SVG blob URL currently shown in the viewer
  var currentSvgText = null; // raw SVG string currently shown (needed for DXF export)
  var zoomLevel = 1; // 0.25 - 4, applied as img width %

  var session = null; // {images, dimensions, unit, description, scale, quality, turns}
  var inLiveSession = false; // true once a drawing has been generated in THIS page load (photos still in memory)
  var currentGalleryEntryId = null; // gallery entry the viewer is currently showing/updating
  var galleryObjectUrls = []; // thumbnail blob URLs — revoked when leaving the gallery
  var lastNonGalleryState = "upload";
  var toastTimer = null;

  var reviewAbortController = null;
  var pendingReviewSvg = null; // revised svg awaiting accept/keep in #review-panel
  var reviewAfterUrl = null; // blob URL for the "revised" thumbnail — revoked on close

  /* ── State switching ─────────────────────────────────────────────────── */
  function showState(name) {
    if (name !== "gallery") lastNonGalleryState = name;
    stateUpload.classList.toggle("hidden", name !== "upload");
    stateProgress.classList.toggle("hidden", name !== "progress");
    stateViewer.classList.toggle("hidden", name !== "viewer");
    stateGallery.classList.toggle("hidden", name !== "gallery");
    wrapEl.classList.toggle("wide", name === "viewer" || name === "gallery");
    if (name !== "gallery") revokeGalleryObjectUrls();
  }

  /* ── Toast (small flash message, e.g. gallery-full / DXF errors) ───────── */
  function showToast(message, isError) {
    toastEl.textContent = message;
    toastEl.classList.toggle("toast-error", !!isError);
    toastEl.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.add("hidden"); }, 3500);
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
      var scaleFactor = MAX_EDGE_PX / longestEdge;
      width = Math.round(width * scaleFactor);
      height = Math.round(height * scaleFactor);
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

  // Turns a bare base64 payload (e.g. the server's annotated_image) back into
  // a blob object URL, so a freshly-marked-up photo can replace a thumbnail
  // without re-uploading anything.
  function b64ToObjectUrl(b64, mimeType) {
    var byteChars = atob(b64);
    var byteNumbers = new Array(byteChars.length);
    for (var i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
    var byteArray = new Uint8Array(byteNumbers);
    var blob = new Blob([byteArray], { type: mimeType });
    return URL.createObjectURL(blob);
  }

  /* ── Photo selection / thumbnails ───────────────────────────────────── */
  function photoKey(file) {
    return file.name + ":" + file.size;
  }

  function addFiles(fileList) {
    var files = Array.prototype.slice.call(fileList);
    var existingKeys = selectedPhotos.map(function (p) { return photoKey(p.file); });
    var rejectedForCount = false;
    var newlyAdded = [];

    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      if (!file.type || file.type.indexOf("image/") !== 0) continue;
      var key = photoKey(file);
      if (existingKeys.indexOf(key) !== -1) continue; // dedupe by name+size
      if (selectedPhotos.length >= MAX_PHOTOS) {
        rejectedForCount = true;
        break;
      }
      var photo = {
        file: file,
        objectUrl: URL.createObjectURL(file),
        b64: null,
        scaleState: "pending", // pending | checking | found | warn | none
        pxPerMm: null,
        markerSizeMm: null
      };
      selectedPhotos.push(photo);
      newlyAdded.push(photo);
      existingKeys.push(key);
    }

    if (rejectedForCount) {
      showPhotoError("Only " + MAX_PHOTOS + " photos allowed — extra photos were not added.");
    } else {
      clearPhotoError();
    }

    renderThumbnails();
    updateGenerateEnabled();

    // Kick off scale detection for the new photos only, one at a time.
    newlyAdded.forEach(function (photo) { scaleQueue.push(photo); });
    runScaleQueue();
  }

  function removePhoto(index) {
    var removed = selectedPhotos.splice(index, 1)[0];
    if (removed) {
      URL.revokeObjectURL(removed.objectUrl);
      if (removed === scaleSourcePhoto) {
        // Reassign scale to the next remaining photo that has a marker, if any.
        scaleSourcePhoto = null;
        for (var i = 0; i < selectedPhotos.length; i++) {
          if (selectedPhotos[i].pxPerMm != null) {
            scaleSourcePhoto = selectedPhotos[i];
            break;
          }
        }
      }
    }
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

      var badge = buildScaleBadge(photo);
      if (badge) thumb.appendChild(badge);

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

  function buildScaleBadge(photo) {
    var badge = document.createElement("span");
    if (photo.scaleState === "checking") {
      badge.className = "thumb-scale-badge checking";
      badge.textContent = "checking…";
    } else if (photo.scaleState === "found") {
      badge.className = "thumb-scale-badge found";
      badge.textContent = "SCALE ✓ " + photo.pxPerMm.toFixed(1) + " px/mm";
    } else if (photo.scaleState === "warn") {
      badge.className = "thumb-scale-badge warn";
      badge.textContent = "ANGLE ⚠ retake square-on";
    } else {
      return null; // pending / none — no badge
    }
    return badge;
  }

  function revokeAllPhotoUrls() {
    selectedPhotos.forEach(function (p) { URL.revokeObjectURL(p.objectUrl); });
    selectedPhotos = [];
    scaleSourcePhoto = null;
    scaleQueue = [];
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

  /* ── Scale detection ─────────────────────────────────────────────────── */
  // Runs one photo through /api/detect-scale at a time (the backend contract
  // expects a single image per call). Non-fatal on any failure: generation
  // must still work even if every check fails or the network is down.
  function runScaleQueue() {
    if (scaleQueueBusy) return;
    scaleQueueBusy = true;
    processNextInScaleQueue();
  }

  function processNextInScaleQueue() {
    if (scaleQueue.length === 0) {
      scaleQueueBusy = false;
      return;
    }
    var photo = scaleQueue.shift();
    if (selectedPhotos.indexOf(photo) === -1) {
      processNextInScaleQueue(); // removed before its turn came up
      return;
    }
    checkPhotoScale(photo).then(processNextInScaleQueue);
  }

  async function checkPhotoScale(photo) {
    try {
      if (!photo.b64) photo.b64 = await downscaleToJpegBase64(photo.file);
    } catch (err) {
      console.warn("BluePrint: could not read photo for scale check", err);
      return;
    }

    photo.scaleState = "checking";
    renderThumbnails();

    try {
      var response = await fetch("/api/detect-scale", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: photo.b64 })
      });
      if (!response.ok) throw new Error("detect-scale HTTP " + response.status);
      var result = await response.json();

      if (selectedPhotos.indexOf(photo) === -1) return; // removed while checking

      if (result && result.found) {
        photo.b64 = result.annotated_image;
        var newUrl = b64ToObjectUrl(photo.b64, "image/jpeg");
        URL.revokeObjectURL(photo.objectUrl);
        photo.objectUrl = newUrl;
        photo.pxPerMm = result.px_per_mm;
        photo.markerSizeMm = result.marker_size_mm;
        photo.scaleState = result.angle_warning ? "warn" : "found";
        if (!scaleSourcePhoto) scaleSourcePhoto = photo; // first marker photo wins
      } else {
        photo.scaleState = "none";
      }
    } catch (err) {
      console.warn("BluePrint: scale detection failed (non-fatal)", err);
      if (selectedPhotos.indexOf(photo) !== -1) photo.scaleState = "none";
    }

    if (selectedPhotos.indexOf(photo) !== -1) renderThumbnails();
  }

  function buildScalePayload() {
    if (!scaleSourcePhoto || scaleSourcePhoto.pxPerMm == null) return null;
    var idx = selectedPhotos.indexOf(scaleSourcePhoto);
    if (idx === -1) return null;
    return {
      px_per_mm: scaleSourcePhoto.pxPerMm,
      marker_photo: idx,
      marker_size_mm: scaleSourcePhoto.markerSizeMm
    };
  }

  /* ── Dimension inputs (dynamic 1-6 row list) ─────────────────────────── */
  // Each row = label <select> (with "custom" -> text input) + value <input>
  // + remove button. The first row is permanent (no remove button). Rows are
  // built entirely in JS; index.html only provides the empty containers.
  function buildDimensionRow(removable) {
    var row = document.createElement("div");
    row.className = "dimension-row";

    var main = document.createElement("div");
    main.className = "dimension-row-main";

    var select = document.createElement("select");
    select.className = "input dim-label-select";
    DIMENSION_LABELS.forEach(function (opt) {
      var o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.text;
      select.appendChild(o);
    });
    main.appendChild(select);

    var valueInput = document.createElement("input");
    valueInput.className = "input dim-value-input";
    valueInput.type = "number";
    valueInput.min = "0.1";
    valueInput.step = "0.1";
    valueInput.placeholder = "value";
    main.appendChild(valueInput);

    var removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "dim-remove-btn";
    removeBtn.setAttribute("aria-label", "Remove dimension");
    removeBtn.textContent = "×";
    if (!removable) removeBtn.classList.add("hidden");
    main.appendChild(removeBtn);

    row.appendChild(main);

    var customInput = document.createElement("input");
    customInput.className = "input dim-custom-input hidden";
    customInput.type = "text";
    customInput.placeholder = "Name this dimension";
    row.appendChild(customInput);

    select.addEventListener("change", function () {
      customInput.classList.toggle("hidden", select.value !== "custom");
      updateGenerateEnabled();
    });
    valueInput.addEventListener("input", updateGenerateEnabled);
    customInput.addEventListener("input", updateGenerateEnabled);
    removeBtn.addEventListener("click", function () {
      row.remove();
      updateAddDimensionBtnState();
      updateGenerateEnabled();
    });

    return row;
  }

  function addDimensionRow() {
    if (dimensionRowsEl.children.length >= MAX_DIMENSIONS) return;
    dimensionRowsEl.appendChild(buildDimensionRow(true));
    updateAddDimensionBtnState();
    updateGenerateEnabled();
  }

  function updateAddDimensionBtnState() {
    addDimensionBtn.disabled = dimensionRowsEl.children.length >= MAX_DIMENSIONS;
  }

  function initDimensionRows() {
    dimensionRowsEl.innerHTML = "";
    dimensionRowsEl.appendChild(buildDimensionRow(false));
    updateAddDimensionBtnState();
  }

  addDimensionBtn.addEventListener("click", addDimensionRow);

  // Reads every row into [{label, value}], value is null when blank/invalid.
  function readDimensionRows() {
    var rows = dimensionRowsEl.querySelectorAll(".dimension-row");
    var result = [];
    for (var i = 0; i < rows.length; i++) {
      var select = rows[i].querySelector(".dim-label-select");
      var customInput = rows[i].querySelector(".dim-custom-input");
      var valueInput = rows[i].querySelector(".dim-value-input");
      var label = select.value === "custom" ? (customInput.value || "").trim() : select.value;
      var parsed = parseFloat(valueInput.value);
      result.push({ label: label, value: isNaN(parsed) ? null : parsed });
    }
    return result;
  }

  function isFormValid() {
    if (selectedPhotos.length < 1 || selectedPhotos.length > MAX_PHOTOS) return false;
    var dims = readDimensionRows();
    if (dims.length < 1) return false;
    for (var i = 0; i < dims.length; i++) {
      if (!dims[i].label) return false;
      if (dims[i].value === null || dims[i].value < 0.1) return false;
    }
    return true;
  }

  function updateGenerateEnabled() {
    generateBtn.disabled = !isFormValid();
  }

  descriptionInput.addEventListener("input", updateGenerateEnabled);

  /* ── Quality pills — locked once a drawing exists in this session ──────── */
  function lockQualityPills(locked) {
    for (var i = 0; i < qualityPills.length; i++) {
      qualityPills[i].classList.toggle("locked", locked);
      var input = qualityPills[i].querySelector("input");
      if (input) input.disabled = locked;
    }
  }

  function getSelectedQuality() {
    var checked = document.querySelector('input[name="quality"]:checked');
    return checked ? checked.value : "fast";
  }

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
      // Every photo needs its base64 payload ready — most already have it
      // from the scale-detection pass, but a check that's still in flight
      // (or failed) must not block generation.
      for (var i = 0; i < selectedPhotos.length; i++) {
        if (!selectedPhotos[i].b64) {
          selectedPhotos[i].b64 = await downscaleToJpegBase64(selectedPhotos[i].file);
        }
      }
      var images = selectedPhotos.map(function (p) { return p.b64; });

      session = {
        images: images,
        dimensions: readDimensionRows(),
        unit: dimensionUnitSelect.value,
        description: (descriptionInput.value || "").trim(),
        scale: buildScalePayload(),
        quality: getSelectedQuality(),
        turns: []
      };

      var payload = {
        images: session.images,
        dimensions: session.dimensions,
        unit: session.unit,
        description: session.description,
        quality: session.quality
      };
      if (session.scale) payload.scale = session.scale;

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

      await consumeStream(response, "generate");
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

  /* ── Shared NDJSON stream reader (generate + refine) ────────────────── */
  async function consumeStream(response, mode, extra) {
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
        handleStreamEvent(JSON.parse(line), mode, extra);
      }
    }

    // flush any final complete line left without a trailing newline
    var tail = buffer.trim();
    if (tail) handleStreamEvent(JSON.parse(tail), mode, extra);
  }

  function handleStreamEvent(event, mode, extra) {
    if (mode === "refine") {
      switch (event.type) {
        case "status":
          refineProgressStatus.textContent = event.message;
          break;
        case "heartbeat":
          break;
        case "progress":
          refineProgressStatus.textContent = "Refining… (" + event.chars + " chars)";
          break;
        case "done":
          completeRefine(event.svg, extra);
          break;
        case "error":
          failRefine(event.message || "Refinement failed.");
          break;
        default:
          break;
      }
      return;
    }

    if (mode === "review") {
      switch (event.type) {
        case "status":
          refineProgressStatus.textContent = event.message;
          break;
        case "heartbeat":
          break;
        case "progress":
          refineProgressStatus.textContent = "Reviewing… (" + event.chars + " chars)";
          break;
        case "done":
          completeReview(event.svg, event.defects);
          break;
        case "error":
          failReview(event.message || "Review failed.");
          break;
        default:
          break;
      }
      return;
    }

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
    showSvgInViewer(svgText);
    resetZoom();

    session.turns.push({ instruction: "", svg: svgText });
    lockQualityPills(true);
    inLiveSession = true;
    refineAbortController = null;

    currentGalleryEntryId = saveGalleryEntry({
      name: session.description || ("Drawing " + new Date().toLocaleString()),
      dimensions: session.dimensions,
      unit: session.unit,
      quality: session.quality,
      svg: svgText
    });

    updateRefineUiForLiveSession();
    updateRefineTurnLabel();
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

  // Shared by generate/refine/gallery-open: swaps the viewer's <img> to a new
  // SVG and keeps the raw text around (needed for the DXF export POST body
  // and the id="geometry" pre-check).
  function showSvgInViewer(svgText) {
    revokeStaleBlobUrl();
    currentSvgText = svgText;
    var blob = new Blob([svgText], { type: "image/svg+xml" });
    currentBlobUrl = URL.createObjectURL(blob);
    drawingImg.src = currentBlobUrl;
    updateDxfButtonState(svgText);
  }

  /* ── Refinement chat ─────────────────────────────────────────────────── */
  refineSendBtn.addEventListener("click", startRefine);
  refineInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      startRefine();
    }
  });

  async function startRefine() {
    if (!session || !inLiveSession) return;
    if (session.turns.length >= MAX_REFINE_TURNS) return;
    var instruction = (refineInput.value || "").trim();
    if (!instruction) return;

    setRefineBusy(true);
    refineProgress.classList.remove("hidden");
    refineProgressStatus.textContent = "Refining…";

    refineAbortController = new AbortController();

    var payload = {
      images: session.images,
      dimensions: session.dimensions,
      unit: session.unit,
      description: session.description,
      scale: session.scale || null,
      quality: session.quality,
      turns: session.turns,
      instruction: instruction
    };

    try {
      var response = await fetch("/api/refine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: refineAbortController.signal
      });

      if (!response.ok) {
        var errMessage = "Something went wrong. Please try again.";
        try {
          var errBody = await response.json();
          if (errBody && errBody.error) errMessage = errBody.error;
        } catch (parseErr) {
          // response body wasn't JSON — fall back to the generic message
        }
        failRefine(errMessage);
        return;
      }

      await consumeStream(response, "refine", instruction);
    } catch (err) {
      if (err && err.name === "AbortError") {
        setRefineBusy(false);
        refineProgress.classList.add("hidden");
        return;
      }
      failRefine("Network error — check your connection and try again.");
    }
  }

  function completeRefine(svgText, instruction) {
    session.turns.push({ instruction: instruction, svg: svgText });
    showSvgInViewer(svgText);
    if (currentGalleryEntryId) updateGalleryEntrySvg(currentGalleryEntryId, svgText);

    refineProgress.classList.add("hidden");
    refineInput.value = "";
    setRefineBusy(false);
    updateRefineTurnLabel();
    enforceRefineTurnCap();
  }

  function failRefine(message) {
    refineProgress.classList.add("hidden");
    setRefineBusy(false);
    showToast(message, true);
  }

  // Shared by refine and an accepted review fix — both consume a "turn".
  function enforceRefineTurnCap() {
    if (session.turns.length >= MAX_REFINE_TURNS) {
      refineInput.disabled = true;
      refineInput.placeholder = "start a new drawing to continue";
      refineSendBtn.disabled = true;
    }
  }

  function setRefineBusy(busy) {
    refineInput.disabled = busy;
    refineSendBtn.disabled = busy;
    reviewBtn.disabled = busy || !inLiveSession;
  }

  function updateRefineTurnLabel() {
    var count = session ? session.turns.length : 0;
    refineTurnLabel.textContent = "Turn " + count + " of " + MAX_REFINE_TURNS;
  }

  function updateRefineUiForLiveSession() {
    refineControls.classList.toggle("hidden", !inLiveSession);
    refineGalleryNote.classList.toggle("hidden", inLiveSession);
    reviewRow.classList.toggle("hidden", !inLiveSession);
    reviewBtn.disabled = !inLiveSession;
    if (!inLiveSession) closeReviewPanel();
  }

  function resetRefineBar() {
    refineInput.value = "";
    refineInput.disabled = false;
    refineInput.placeholder = "Refine: e.g. make the side view larger, add a dimension to the hole";
    refineSendBtn.disabled = false;
    refineProgress.classList.add("hidden");
    refineTurnLabel.textContent = "Turn 0 of " + MAX_REFINE_TURNS;
    refineControls.classList.remove("hidden");
    refineGalleryNote.classList.add("hidden");
    reviewRow.classList.remove("hidden");
    reviewBtn.disabled = false;
    closeReviewPanel();
  }

  /* ── Review & fix ─────────────────────────────────────────────────────
     Sends the currently displayed svg back for a single-round defect pass.
     Reuses the same inline progress strip as refine (mode="review" in
     consumeStream / handleStreamEvent) — only one of refine/review can run
     at a time, enforced by disabling both sets of controls while busy. */
  reviewBtn.addEventListener("click", startReview);
  reviewCloseBtn.addEventListener("click", closeReviewPanel);
  reviewKeepBtn.addEventListener("click", closeReviewPanel);
  reviewAcceptBtn.addEventListener("click", acceptReviewFix);

  function setReviewBusy(busy) {
    reviewBtn.disabled = busy || !inLiveSession;
    refineInput.disabled = busy;
    refineSendBtn.disabled = busy;
  }

  async function startReview() {
    if (!session || !inLiveSession) return;
    if (reviewBtn.disabled || refineSendBtn.disabled) return; // something else is already streaming
    if (!currentSvgText) return;

    closeReviewPanel();
    setReviewBusy(true);
    refineProgress.classList.remove("hidden");
    refineProgressStatus.textContent = "Rendering preview…";

    reviewAbortController = new AbortController();

    var payload = {
      images: session.images,
      dimensions: session.dimensions,
      unit: session.unit,
      description: session.description,
      scale: session.scale || null,
      quality: session.quality,
      svg: currentSvgText
    };

    try {
      var response = await fetch("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: reviewAbortController.signal
      });

      if (!response.ok) {
        var errMessage = "Something went wrong. Please try again.";
        try {
          var errBody = await response.json();
          if (errBody && errBody.error) errMessage = errBody.error;
        } catch (parseErr) {
          // response body wasn't JSON — fall back to the generic message
        }
        failReview(errMessage);
        return;
      }

      await consumeStream(response, "review");
    } catch (err) {
      if (err && err.name === "AbortError") {
        setReviewBusy(false);
        refineProgress.classList.add("hidden");
        return;
      }
      failReview("Network error — check your connection and try again.");
    }
  }

  function completeReview(svgText, defects) {
    refineProgress.classList.add("hidden");
    setReviewBusy(false);

    if (!svgText) {
      showToast("Review found no defects ✓");
      return;
    }

    pendingReviewSvg = svgText;
    renderReviewDefects(defects || []);

    reviewThumbBefore.src = currentBlobUrl || "";
    revokeReviewAfterUrl();
    var blob = new Blob([svgText], { type: "image/svg+xml" });
    reviewAfterUrl = URL.createObjectURL(blob);
    reviewThumbAfter.src = reviewAfterUrl;

    reviewPanel.classList.remove("hidden");
  }

  function renderReviewDefects(defects) {
    reviewDefectsEl.innerHTML = "";
    if (defects.length === 0) {
      var li = document.createElement("li");
      li.textContent = "Issues found — see revised drawing.";
      reviewDefectsEl.appendChild(li);
      return;
    }
    defects.forEach(function (d) {
      var li = document.createElement("li");
      li.textContent = d;
      reviewDefectsEl.appendChild(li);
    });
  }

  function failReview(message) {
    refineProgress.classList.add("hidden");
    setReviewBusy(false);
    showToast(message, true);
  }

  function acceptReviewFix() {
    if (!pendingReviewSvg) return;
    var svgText = pendingReviewSvg;
    session.turns.push({ instruction: "[review fix]", svg: svgText });
    showSvgInViewer(svgText);
    if (currentGalleryEntryId) updateGalleryEntrySvg(currentGalleryEntryId, svgText);
    updateRefineTurnLabel();
    enforceRefineTurnCap();
    closeReviewPanel();
  }

  function closeReviewPanel() {
    reviewPanel.classList.add("hidden");
    pendingReviewSvg = null;
    revokeReviewAfterUrl();
  }

  function revokeReviewAfterUrl() {
    if (reviewAfterUrl) {
      URL.revokeObjectURL(reviewAfterUrl);
      reviewAfterUrl = null;
    }
  }

  /* ── DXF export ──────────────────────────────────────────────────────── */
  function updateDxfButtonState(svgText) {
    var hasGeometry = typeof svgText === "string" && svgText.indexOf('id="geometry"') !== -1;
    dxfBtn.disabled = !hasGeometry;
    dxfBtn.title = hasGeometry
      ? "In Fusion: Insert > Insert DXF, units mm"
      : "This drawing predates DXF support";
  }

  dxfBtn.addEventListener("click", async function () {
    if (dxfBtn.disabled || !currentSvgText) return;
    dxfBtn.disabled = true;
    try {
      var response = await fetch("/api/export-dxf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ svg: currentSvgText })
      });

      if (!response.ok) {
        var message = "Could not export DXF.";
        try {
          var body = await response.json();
          if (body && body.message) message = body.message;
        } catch (parseErr) {
          // non-JSON error body — fall back to the generic message
        }
        showToast(message, true);
        return;
      }

      var blob = await response.blob();
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = "blueprint.dxf";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast("Network error exporting DXF.", true);
    } finally {
      updateDxfButtonState(currentSvgText);
    }
  });

  /* ── Gallery: localStorage-backed saved drawings ─────────────────────── */
  function loadGallery() {
    try {
      var raw = localStorage.getItem(GALLERY_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (err) {
      return [];
    }
  }

  function persistGallery(items) {
    localStorage.setItem(GALLERY_KEY, JSON.stringify(items));
  }

  function saveGalleryEntry(fields) {
    var items = loadGallery();
    var entry = {
      id: Date.now() + "-" + Math.random().toString(36).slice(2, 8),
      name: fields.name,
      date: new Date().toISOString(),
      dimensions: fields.dimensions,
      unit: fields.unit,
      quality: fields.quality,
      svg: fields.svg
    };
    items.push(entry);
    try {
      persistGallery(items);
    } catch (err) {
      if (err && err.name === "QuotaExceededError") {
        showToast("Gallery full — delete old drawings", true);
      }
      updateGalleryButtonCount();
      return null; // still showing the drawing in the viewer either way
    }
    updateGalleryButtonCount();
    return entry.id;
  }

  function updateGalleryEntrySvg(id, svgText) {
    var items = loadGallery();
    var found = false;
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === id) {
        items[i].svg = svgText;
        found = true;
        break;
      }
    }
    if (!found) return;
    try {
      persistGallery(items);
    } catch (err) {
      if (err && err.name === "QuotaExceededError") {
        showToast("Gallery full — delete old drawings", true);
      }
    }
  }

  function deleteGalleryEntry(id) {
    var items = loadGallery().filter(function (e) { return e.id !== id; });
    persistGallery(items);
    if (currentGalleryEntryId === id) currentGalleryEntryId = null;
    updateGalleryButtonCount();
    renderGallery();
  }

  function renameGalleryEntry(id, name) {
    var items = loadGallery();
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === id) {
        items[i].name = name || items[i].name;
        break;
      }
    }
    persistGallery(items);
  }

  function updateGalleryButtonCount() {
    galleryBtn.textContent = "Gallery (" + loadGallery().length + ")";
  }

  function revokeGalleryObjectUrls() {
    galleryObjectUrls.forEach(function (u) { URL.revokeObjectURL(u); });
    galleryObjectUrls = [];
  }

  function formatGalleryDate(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return months[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
  }

  function renderGallery() {
    revokeGalleryObjectUrls();
    var items = loadGallery();
    updateGalleryButtonCount();
    galleryGrid.innerHTML = "";

    if (items.length === 0) {
      galleryEmpty.classList.remove("hidden");
      galleryGrid.classList.add("hidden");
      return;
    }
    galleryEmpty.classList.add("hidden");
    galleryGrid.classList.remove("hidden");

    items.slice().reverse().forEach(function (entry) {
      galleryGrid.appendChild(buildGalleryCard(entry));
    });
  }

  // Old gallery entries (pre multi-dimension) stored a single
  // dimension_label/dimension_value pair instead of a dimensions array —
  // map them to a one-entry array on read so old cards render instead of
  // crashing.
  function getEntryDimensions(entry) {
    if (entry.dimensions && entry.dimensions.length) return entry.dimensions;
    if (entry.dimension_label) {
      return [{ label: entry.dimension_label, value: entry.dimension_value }];
    }
    return [];
  }

  function formatEntryDimensions(entry) {
    var dims = getEntryDimensions(entry);
    var unit = entry.unit || "";
    if (dims.length === 0) return "—";
    return dims.map(function (d) { return d.label + " " + d.value + " " + unit; }).join(" · ");
  }

  function buildGalleryCard(entry) {
    var card = document.createElement("div");
    card.className = "gallery-card";

    var thumbWrap = document.createElement("div");
    thumbWrap.className = "gallery-thumb";
    var blob = new Blob([entry.svg], { type: "image/svg+xml" });
    var url = URL.createObjectURL(blob);
    galleryObjectUrls.push(url);
    var img = document.createElement("img");
    img.src = url;
    img.alt = entry.name;
    thumbWrap.appendChild(img);
    card.appendChild(thumbWrap);

    var info = document.createElement("div");
    info.className = "gallery-info";

    var nameEl = document.createElement("span");
    nameEl.className = "gallery-name";
    nameEl.textContent = entry.name;
    nameEl.title = "Click to rename";
    nameEl.addEventListener("click", function () { startRenameGalleryEntry(entry.id, nameEl); });
    info.appendChild(nameEl);

    var dateEl = document.createElement("span");
    dateEl.className = "gallery-date";
    dateEl.textContent = formatGalleryDate(entry.date);
    info.appendChild(dateEl);

    var dimEl = document.createElement("span");
    dimEl.className = "gallery-dim";
    dimEl.textContent = formatEntryDimensions(entry) + " · " + entry.quality;
    info.appendChild(dimEl);

    card.appendChild(info);

    var actions = document.createElement("div");
    actions.className = "gallery-actions";

    var openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "btn btn-sm";
    openBtn.textContent = "Open";
    openBtn.addEventListener("click", function () { openGalleryEntry(entry.id); });
    actions.appendChild(openBtn);

    var delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-sm btn-ghost gallery-delete";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", function () { deleteGalleryEntry(entry.id); });
    actions.appendChild(delBtn);

    card.appendChild(actions);
    return card;
  }

  function startRenameGalleryEntry(id, spanEl) {
    var input = document.createElement("input");
    input.type = "text";
    input.className = "gallery-name-input";
    input.value = spanEl.textContent;
    spanEl.replaceWith(input);
    input.focus();
    input.select();

    function commit() {
      var newName = input.value.trim() || input.defaultValue;
      renameGalleryEntry(id, newName);
      renderGallery();
    }

    input.addEventListener("blur", commit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        input.blur();
      } else if (e.key === "Escape") {
        e.preventDefault();
        renderGallery();
      }
    });
  }

  function openGalleryEntry(id) {
    var items = loadGallery();
    var entry = null;
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === id) { entry = items[i]; break; }
    }
    if (!entry) return;

    if (refineAbortController) refineAbortController.abort();
    if (reviewAbortController) reviewAbortController.abort();
    inLiveSession = false;
    currentGalleryEntryId = entry.id;

    showSvgInViewer(entry.svg);
    resetZoom();
    updateRefineUiForLiveSession();
    showState("viewer");
  }

  galleryBtn.addEventListener("click", function () {
    renderGallery();
    showState("gallery");
  });
  galleryBackBtn.addEventListener("click", function () {
    showState(lastNonGalleryState === "gallery" ? "upload" : lastNonGalleryState);
  });

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
    if (refineAbortController) refineAbortController.abort();
    if (reviewAbortController) reviewAbortController.abort();
    revokeStaleBlobUrl();
    currentSvgText = null;
    drawingImg.src = "";

    session = null;
    inLiveSession = false;
    currentGalleryEntryId = null;
    lockQualityPills(false);
    resetRefineBar();

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
    initDimensionRows();
    dimensionUnitSelect.value = "mm";
    var fastRadio = document.querySelector('input[name="quality"][value="fast"]');
    if (fastRadio) fastRadio.checked = true;
    updateGenerateEnabled();
  }

  /* ── Init ────────────────────────────────────────────────────────────── */
  initDimensionRows();
  updateGenerateEnabled();
  updateGalleryButtonCount();
  resetRefineBar();

  // PWA: register the service worker (served by Flask at root scope from
  // static/js/sw.js) and fire a fire-and-forget wake ping so a plain-tab
  // visit also nudges the Render free-tier instance awake.
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }
  fetch("/healthz").catch(function () {});
})();
