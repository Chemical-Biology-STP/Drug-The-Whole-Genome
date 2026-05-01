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
    initAutoScreeningMode();
    initAutoFillTargetName();
    initLibrarySourceToggle();
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
   Auto Screening Mode Selection
   ========================================================================= */

var LARGE_SCALE_THRESHOLD = 1000000; // 1 million compounds

/**
 * Automatically choose Standard vs Large-Scale based on library size.
 * Watches both the file upload input and the pre-encoded library selector.
 */
function initAutoScreeningMode() {
    var libraryFile    = document.getElementById('library_file');
    var preencodedSel  = document.getElementById('preencoded_library');
    var libSourceRadios = document.querySelectorAll('input[name="library_source"]');

    if (libraryFile) {
        libraryFile.addEventListener('change', function () {
            estimateUploadedLibrarySize(this.files[0]);
        });
    }

    if (preencodedSel) {
        preencodedSel.addEventListener('change', function () {
            var opt = this.options[this.selectedIndex];
            var count = parseInt(opt.getAttribute('data-count'), 10);
            if (!isNaN(count)) {
                setScreeningMode(count);
            } else {
                setScreeningMode(null);
            }
        });
    }

    // Re-evaluate when the user switches between upload / pre-encoded
    libSourceRadios.forEach(function (radio) {
        radio.addEventListener('change', function () {
            if (this.value === 'preencoded' && preencodedSel) {
                var opt = preencodedSel.options[preencodedSel.selectedIndex];
                var count = parseInt(opt.getAttribute('data-count'), 10);
                setScreeningMode(isNaN(count) ? null : count);
            } else {
                // Switched back to upload — re-check the file if one is selected
                if (libraryFile && libraryFile.files && libraryFile.files.length > 0) {
                    estimateUploadedLibrarySize(libraryFile.files[0]);
                } else {
                    setScreeningMode(null);
                }
            }
        });
    });
}

/**
 * Estimate compound count from an uploaded file and update the mode.
 * - .sdf: count "$$$$" record separators
 * - .smi / .smiles / .txt: count non-empty lines
 * Uses a FileReader to read the file content in the browser.
 * @param {File} file
 */
function estimateUploadedLibrarySize(file) {
    if (!file) { setScreeningMode(null); return; }

    var ext = file.name.split('.').pop().toLowerCase();
    var reader = new FileReader();

    reader.onload = function (e) {
        var text = e.target.result;
        var count;
        if (ext === 'sdf') {
            // Each molecule ends with "$$$$"
            count = (text.match(/\$\$\$\$/g) || []).length;
        } else {
            // SMILES / text: one molecule per non-empty line
            count = text.split('\n').filter(function (l) {
                return l.trim().length > 0;
            }).length;
        }
        setScreeningMode(count);
    };

    reader.onerror = function () { setScreeningMode(null); };
    reader.readAsText(file);
}

/**
 * Set the hidden screening_mode input and update the visible badge.
 * @param {number|null} compoundCount  null means unknown
 */
function setScreeningMode(compoundCount) {
    var input  = document.getElementById('screening_mode');
    var badge  = document.getElementById('screening-mode-badge');
    var reason = document.getElementById('screening-mode-reason');
    var largeScaleFields = document.querySelector('.large-scale-fields');

    if (!input || !badge) return;

    var mode, label, badgeClass, reasonText;

    if (compoundCount === null || isNaN(compoundCount)) {
        mode       = 'standard';
        label      = 'Standard';
        badgeClass = 'bg-secondary';
        reasonText = 'Select a library to auto-detect.';
    } else if (compoundCount > LARGE_SCALE_THRESHOLD) {
        mode       = 'large_scale';
        label      = 'Large-Scale';
        badgeClass = 'bg-warning text-dark';
        reasonText = compoundCount.toLocaleString() + ' compounds — parallel processing required.';
    } else {
        mode       = 'standard';
        label      = 'Standard';
        badgeClass = 'bg-success';
        reasonText = compoundCount.toLocaleString() + ' compounds — fits in a single job.';
    }

    input.value = mode;
    badge.textContent = label;
    badge.className = 'badge fs-6 px-3 py-2 ' + badgeClass;
    if (reason) reason.textContent = reasonText;

    // Show/hide large-scale advanced fields
    if (largeScaleFields) {
        if (mode === 'large_scale') {
            largeScaleFields.classList.add('active');
        } else {
            largeScaleFields.classList.remove('active');
        }
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
   Library Source Toggle
   ========================================================================= */

/**
 * Show/hide the file upload vs pre-encoded library selector based on the
 * selected library_source radio button.
 */
function initLibrarySourceToggle() {
    var radios = document.querySelectorAll('input[name="library_source"]');
    if (radios.length === 0) return;

    radios.forEach(function (radio) {
        radio.addEventListener('change', function () {
            updateLibrarySourceFields(this.value);
        });
    });

    // Set initial state
    var checked = document.querySelector('input[name="library_source"]:checked');
    if (checked) {
        updateLibrarySourceFields(checked.value);
    }
}

/**
 * Show the appropriate library input section.
 * @param {string} source - 'upload' or 'preencoded'
 */
function updateLibrarySourceFields(source) {
    var uploadFields = document.getElementById('lib-upload-fields');
    var preencodedFields = document.getElementById('lib-preencoded-fields');

    if (!uploadFields || !preencodedFields) return;

    if (source === 'preencoded') {
        uploadFields.style.display = 'none';
        preencodedFields.style.display = 'block';
    } else {
        uploadFields.style.display = 'block';
        preencodedFields.style.display = 'none';
    }
}
