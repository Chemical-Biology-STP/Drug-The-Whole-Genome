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
