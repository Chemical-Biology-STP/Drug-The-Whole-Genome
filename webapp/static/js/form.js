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
    var uploadPanel = document.getElementById('lib-upload-panel');
    var hpcPanel    = document.getElementById('lib-hpc-panel');

    if (!uploadTab || !hpcTab) return;

    function updateLibraryPanel() {
        if (hpcTab.checked) {
            uploadPanel.classList.add('d-none');
            hpcPanel.classList.remove('d-none');
            document.getElementById('library_upload_path').value = '';
        } else {
            hpcPanel.classList.add('d-none');
            uploadPanel.classList.remove('d-none');
            document.getElementById('library_hpc_path').value = '';
        }
    }

    uploadTab.addEventListener('change', updateLibraryPanel);
    hpcTab.addEventListener('change', updateLibraryPanel);
    updateLibraryPanel();

    // ── Chunked uploader ────────────────────────────────────────────────────
    var CHUNK_SIZE = 50 * 1024 * 1024; // 50 MB per chunk

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

            // For small files (< 100 MB) use the regular hidden file input
            if (file.size < 100 * 1024 * 1024) {
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

        function uploadNext(index) {
            if (cancelled) return;
            if (index >= totalChunks) return; // done handled by server response

            // Skip already-received chunks
            if (alreadyReceived.indexOf(index) !== -1) {
                uploadNext(index + 1);
                return;
            }

            var start = index * CHUNK_SIZE;
            var end   = Math.min(start + CHUNK_SIZE, file.size);
            var chunk = file.slice(start, end);

            fetch('/upload/library', {
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
                if (cancelled) return;
                uploadedChunks++;
                var pct = Math.round((uploadedChunks / totalChunks) * 100);
                progressBar.style.width = pct + '%';
                progressPct.textContent = pct + '%';
                statusLabel.textContent = 'Uploading ' + file.name + ' — ' +
                    formatBytes(Math.min(uploadedChunks * CHUNK_SIZE, file.size)) +
                    ' / ' + formatBytes(file.size);

                if (data.done) {
                    // Upload complete
                    uploadPathField.value = data.path;
                    progressBar.style.width = '100%';
                    progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
                    progressBar.classList.add('bg-success');
                    progressPct.textContent = '100%';
                    statusLabel.textContent = '✓ ' + file.name + ' ready (' + formatBytes(file.size) + ')';
                    cancelBtn.classList.add('d-none');
                    setSubmitEnabled(true);
                } else {
                    uploadNext(index + 1);
                }
            })
            .catch(function(err) {
                if (cancelled) return;
                progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
                progressBar.classList.add('bg-danger');
                statusLabel.textContent = 'Upload failed: ' + err.message + ' — click Cancel and retry';
                setSubmitEnabled(true);
            });
        }

        // Find the first chunk not yet received
        var firstMissing = 0;
        while (alreadyReceived.indexOf(firstMissing) !== -1) firstMissing++;
        uploadNext(firstMissing);
    }

    function generateId() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    }
});
