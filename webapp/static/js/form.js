/**
 * DrugCLIP Web Application - Form Interactivity
 *
 * Handles:
 * - Binding site selector: show/hide relevant input fields based on radio selection
 * - Screening mode toggle: show/hide large-scale fields
 * - Auto-fill target name from PDB filename
 *
 * Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 6.1, 6.2, 6.3, 7.3
 */

document.addEventListener('DOMContentLoaded', function () {
    initBindingSiteSelector();
    initScreeningModeToggle();
    initAutoFillTargetName();
});

/* =========================================================================
   Binding Site Selector
   ========================================================================= */

/**
 * Show/hide binding site input fields based on the selected radio button.
 * Only one method's fields are visible at a time.
 */
function initBindingSiteSelector() {
    var radios = document.querySelectorAll('input[name="binding_site_method"]');
    if (radios.length === 0) return;

    radios.forEach(function (radio) {
        radio.addEventListener('change', function () {
            updateBindingSiteFields(this.value);
        });
    });

    // Set initial state based on any pre-selected radio
    var checked = document.querySelector('input[name="binding_site_method"]:checked');
    if (checked) {
        updateBindingSiteFields(checked.value);
    }
}

/**
 * Show the field group matching the selected method, hide all others.
 * @param {string} method - The selected binding site method value
 */
function updateBindingSiteFields(method) {
    var allFields = document.querySelectorAll('.binding-site-fields');
    allFields.forEach(function (el) {
        el.classList.remove('active');
    });

    var target = document.getElementById('fields-' + method);
    if (target) {
        target.classList.add('active');
    }
}

/* =========================================================================
   Screening Mode Toggle
   ========================================================================= */

/**
 * Show/hide large-scale screening fields (chunk size, partition, max parallel)
 * based on the selected screening mode.
 */
function initScreeningModeToggle() {
    var radios = document.querySelectorAll('input[name="screening_mode"]');
    if (radios.length === 0) return;

    radios.forEach(function (radio) {
        radio.addEventListener('change', function () {
            updateScreeningModeFields(this.value);
        });
    });

    // Set initial state based on any pre-selected radio
    var checked = document.querySelector('input[name="screening_mode"]:checked');
    if (checked) {
        updateScreeningModeFields(checked.value);
    }
}

/**
 * Show large-scale fields when "large_scale" is selected, hide otherwise.
 * @param {string} mode - The selected screening mode value
 */
function updateScreeningModeFields(mode) {
    var largeScaleFields = document.querySelector('.large-scale-fields');
    if (!largeScaleFields) return;

    if (mode === 'large_scale') {
        largeScaleFields.classList.add('active');
    } else {
        largeScaleFields.classList.remove('active');
    }
}

/* =========================================================================
   Auto-fill Target Name from PDB Filename
   ========================================================================= */

/**
 * When a PDB file is selected, auto-fill the target name field with the
 * filename stripped of its .pdb extension.
 */
function initAutoFillTargetName() {
    var pdbInput = document.getElementById('pdb_file');
    var targetNameInput = document.getElementById('target_name');

    if (!pdbInput || !targetNameInput) return;

    pdbInput.addEventListener('change', function () {
        if (this.files && this.files.length > 0) {
            var filename = this.files[0].name;
            // Strip the .pdb extension (case-insensitive)
            var targetName = filename.replace(/\.pdb$/i, '');
            targetNameInput.value = targetName;
        }
    });
}

/* =========================================================================
   Library Source Toggle + Chunked Upload
   ========================================================================= */

document.addEventListener('DOMContentLoaded', function () {
    // ── Tab toggle ──────────────────────────────────────────────────────────
    var uploadTab   = document.getElementById('lib-upload-tab');
    var hpcTab      = document.getElementById('lib-hpc-tab');
    var savedTab    = document.getElementById('lib-saved-tab');
    var uploadPanel = document.getElementById('lib-upload-panel');
    var hpcPanel    = document.getElementById('lib-hpc-panel');
    var savedPanel  = document.getElementById('lib-saved-panel');

    if (!uploadTab || !hpcTab) return;

    function updateLibraryPanel() {
        uploadPanel.classList.add('d-none');
        hpcPanel.classList.add('d-none');
        if (savedPanel) savedPanel.classList.add('d-none');

        // Clear all library fields
        document.getElementById('library_upload_path').value = '';
        var hpcInput = document.getElementById('library_hpc_path');
        if (hpcInput) hpcInput.value = '';
        var savedPath = document.getElementById('library_saved_path');
        if (savedPath) savedPath.value = '';

        if (hpcTab.checked) {
            hpcPanel.classList.remove('d-none');
        } else if (savedTab && savedTab.checked) {
            if (savedPanel) savedPanel.classList.remove('d-none');
            loadSavedLibraries();
        } else {
            uploadPanel.classList.remove('d-none');
        }
    }

    uploadTab.addEventListener('change', updateLibraryPanel);
    hpcTab.addEventListener('change', updateLibraryPanel);
    if (savedTab) savedTab.addEventListener('change', updateLibraryPanel);
    updateLibraryPanel();

    // ── Saved libraries loader ──────────────────────────────────────────────
    var savedLibrariesLoaded = false;

    function loadSavedLibraries() {
        if (savedLibrariesLoaded) return;
        var select = document.getElementById('lib-saved-select');
        if (!select) return;

        fetch('/api/libraries')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                savedLibrariesLoaded = true;
                select.innerHTML = '';
                var libs = data.libraries || [];
                if (libs.length === 0) {
                    select.innerHTML = '<option value="">No saved libraries yet — upload one first</option>';
                    return;
                }
                var placeholder = document.createElement('option');
                placeholder.value = '';
                placeholder.textContent = '— Select a library —';
                select.appendChild(placeholder);
                libs.forEach(function(lib) {
                    var opt = document.createElement('option');
                    opt.value = lib.path;
                    opt.textContent = lib.name + '  (' + lib.size + ')';
                    select.appendChild(opt);
                });
            })
            .catch(function() {
                select.innerHTML = '<option value="">Could not load libraries — check HPC connection</option>';
            });
    }

    var savedSelect = document.getElementById('lib-saved-select');
    if (savedSelect) {
        savedSelect.addEventListener('change', function() {
            var savedPath = document.getElementById('library_saved_path');
            if (savedPath) savedPath.value = this.value;
        });
    }

    // ── Chunked uploader ────────────────────────────────────────────────────
    var CHUNK_SIZE = 10 * 1024 * 1024;  // 10 MB per chunk
    var MAX_PARALLEL = 5;               // upload 5 chunks simultaneously

    var fileInput     = document.getElementById('library_file_input');
    var progressWrap  = document.getElementById('lib-upload-progress');
    var progressBar   = document.getElementById('lib-upload-bar');
    var progressPct   = document.getElementById('lib-upload-pct');
    var statusLabel   = document.getElementById('lib-upload-status');
    var cancelBtn     = document.getElementById('lib-upload-cancel');
    var uploadPathField = document.getElementById('library_upload_path');
    var submitBtn     = document.getElementById('submit-btn');
    var submitWarning = document.getElementById('submit-upload-warning');

    var currentUploadId = null;
    var cancelled = false;

    function formatBytes(bytes) {
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    }

    function setSubmitEnabled(enabled) {
        if (!submitBtn) return;
        submitBtn.disabled = !enabled;
        if (submitWarning) {
            submitWarning.classList.toggle('d-none', enabled);
        }
    }

    if (fileInput) {
        fileInput.addEventListener('change', function () {
            var file = fileInput.files[0];
            if (!file) return;

            // For small files (< 20 MB) use the regular hidden file input
            if (file.size < 20 * 1024 * 1024) {
                var regularInput = document.getElementById('library_file');
                // Transfer the file to the hidden regular input via DataTransfer
                try {
                    var dt = new DataTransfer();
                    dt.items.add(file);
                    regularInput.files = dt.files;
                } catch (e) {
                    // DataTransfer not supported — fall through to chunked
                }
                if (regularInput.files && regularInput.files.length > 0) {
                    uploadPathField.value = '';
                    statusLabel.textContent = 'Ready (' + formatBytes(file.size) + ')';
                    progressWrap.classList.remove('d-none');
                    progressBar.style.width = '100%';
                    progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
                    progressBar.classList.add('bg-success');
                    progressPct.textContent = '100%';
                    setSubmitEnabled(true);
                    return;
                }
            }

            // Chunked upload for large files
            startChunkedUpload(file);
        });
    }

    if (cancelBtn) {
        cancelBtn.addEventListener('click', function () {
            cancelled = true;
            if (currentUploadId) {
                fetch('/upload/library/' + currentUploadId, { method: 'DELETE' }).catch(function(){});
            }
            resetUploadUI();
        });
    }

    function resetUploadUI() {
        progressWrap.classList.add('d-none');
        progressBar.style.width = '0%';
        progressBar.classList.add('progress-bar-animated', 'progress-bar-striped');
        progressBar.classList.remove('bg-success', 'bg-danger');
        progressPct.textContent = '0%';
        statusLabel.textContent = 'Uploading…';
        cancelBtn.classList.add('d-none');
        uploadPathField.value = '';
        currentUploadId = null;
        cancelled = false;
        setSubmitEnabled(true);
    }

    function startChunkedUpload(file) {
        cancelled = false;
        currentUploadId = generateId();
        var totalChunks = Math.ceil(file.size / CHUNK_SIZE);
        var uploadedChunks = 0;

        progressWrap.classList.remove('d-none');
        cancelBtn.classList.remove('d-none');
        setSubmitEnabled(false);
        uploadPathField.value = '';

        // Check for existing partial upload to resume
        fetch('/upload/library/' + currentUploadId)
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(meta) {
                var received = (meta && meta.received_chunks) ? meta.received_chunks : [];
                uploadChunks(file, currentUploadId, totalChunks, received, uploadedChunks);
            })
            .catch(function() {
                uploadChunks(file, currentUploadId, totalChunks, [], 0);
            });
    }

    function uploadChunks(file, uploadId, totalChunks, alreadyReceived, startCount) {
        var uploadedChunks = startCount + alreadyReceived.length;
        var alreadySet = new Set(alreadyReceived);

        // Build queue of chunk indices still to upload
        var queue = [];
        for (var i = 0; i < totalChunks; i++) {
            if (!alreadySet.has(i)) queue.push(i);
        }

        var failed = false;

        function uploadChunk(index) {
            if (cancelled || failed) return Promise.resolve();

            var start = index * CHUNK_SIZE;
            var end   = Math.min(start + CHUNK_SIZE, file.size);
            var chunk = file.slice(start, end);

            return fetch('/upload/library', {
                method: 'POST',
                headers: {
                    'X-Upload-Id':    uploadId,
                    'X-Chunk-Index':  String(index),
                    'X-Total-Chunks': String(totalChunks),
                    'X-Filename':     file.name,
                    'Content-Type':   'application/octet-stream',
                },
                body: chunk,
            })
            .then(function(r) {
                if (!r.ok) throw new Error('Server error ' + r.status);
                return r.json();
            })
            .then(function(data) {
                if (cancelled || failed) return;
                uploadedChunks++;
                var pct = Math.round((uploadedChunks / totalChunks) * 100);
                progressBar.style.width = pct + '%';
                progressPct.textContent = pct + '%';
                statusLabel.textContent = 'Uploading ' + file.name + ' — ' +
                    formatBytes(Math.min(uploadedChunks * CHUNK_SIZE, file.size)) +
                    ' / ' + formatBytes(file.size);

                if (data.done) {
                    uploadPathField.value = data.path;
                    progressBar.style.width = '100%';
                    progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
                    progressBar.classList.add('bg-success');
                    progressPct.textContent = '100%';
                    statusLabel.textContent = '✓ ' + file.name + ' ready (' + formatBytes(file.size) + ')';
                    cancelBtn.classList.add('d-none');
                    setSubmitEnabled(true);
                }
            });
        }

        // Worker: pulls from queue and uploads, then picks the next chunk
        function worker() {
            if (cancelled || failed || queue.length === 0) return Promise.resolve();
            var index = queue.shift();
            return uploadChunk(index)
                .then(function() { return worker(); })
                .catch(function(err) {
                    if (cancelled) return;
                    failed = true;
                    progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
                    progressBar.classList.add('bg-danger');
                    statusLabel.textContent = 'Upload failed: ' + err.message + ' — click Cancel and retry';
                    setSubmitEnabled(true);
                });
        }

        // Launch MAX_PARALLEL workers simultaneously
        var workers = [];
        for (var w = 0; w < Math.min(MAX_PARALLEL, queue.length); w++) {
            workers.push(worker());
        }
    }

    function generateId() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    }
});
