# Implementation Plan: DrugCLIP Web Application

## Overview

Build a Flask web application that wraps the existing DrugCLIP virtual screening pipeline, allowing researchers to submit, monitor, and retrieve HPC screening jobs from a browser. The implementation follows the design's component structure: Flask app factory, service layer (SlurmClient, FileUploadHandler, JobSubmissionService, JobMonitor, JobStore), Jinja2 templates with Bootstrap 5, and a background polling thread. All code lives in `webapp/` and delegates heavy computation to the existing shell scripts via subprocess.

## Tasks

- [x] 1. Set up project structure, dependencies, and Flask app factory
  - [x] 1.1 Add webapp dependencies to pixi.toml
    - Add `flask`, `flask-session`, and `hypothesis` to `[pypi-dependencies]` in the project-root `pixi.toml`
    - _Requirements: 1.3_
  - [x] 1.2 Create webapp directory structure and __init__ files
    - Create the full directory tree: `webapp/`, `webapp/routes/`, `webapp/services/`, `webapp/templates/`, `webapp/static/css/`, `webapp/static/js/`, `webapp/data/`, `webapp/tests/`
    - Add `__init__.py` files to `webapp/`, `webapp/routes/`, `webapp/services/`, `webapp/tests/`
    - Initialize `webapp/data/jobs.json` with `{"jobs": []}`
    - _Requirements: 1.1_
  - [x] 1.3 Create webapp/config.py with application configuration
    - Define configuration constants: `UPLOAD_FOLDER`, `MAX_FILE_SIZE` (500 MB), `POLL_INTERVAL` (30s), `PROJECT_ROOT`, `ALLOWED_EXTENSIONS` per file type, `SESSION_TYPE` (filesystem), `RESULTS_PER_PAGE` (50)
    - _Requirements: 1.1, 7.1, 7.2, 10.2, 12.1_
  - [x] 1.4 Create webapp/app.py with Flask app factory and entry point
    - Implement `create_app()` factory function that configures Flask, registers blueprints, sets up server-side sessions (flask-session with filesystem backend), configures `MAX_CONTENT_LENGTH`, registers error handlers (404, 403, 500 with reference UUID), and starts the JobMonitor background thread
    - Add `if __name__ == '__main__'` block to run the dev server
    - _Requirements: 1.2, 11.1, 12.3_

- [x] 2. Implement data models and validation
  - [x] 2.1 Create webapp/services/models.py with JobParams and JobRecord dataclasses
    - Define `JobParams` dataclass with all form fields (session_id, pdb_path, library_path, binding_site_method, ligand_path, residue_name, center_x/y/z, binding_residues, chain_id, cutoff, target_name, top_fraction, screening_mode, chunk_size, partition, max_parallel)
    - Define `JobRecord` dataclass with all persistent fields (job_id, session_id, target_name, library_name, screening_mode, status, submitted_at, updated_at, params, job_dir, log_path, results_path, error_message, child_job_ids)
    - Implement `to_dict()` and `from_dict()` methods on both dataclasses for JSON serialization
    - _Requirements: 8.5, 9.2_
  - [x] 2.2 Create webapp/services/validation.py with parameter validation functions
    - Implement `validate_file_extension(filename, file_type)` that checks against allowed sets (`.pdb` for receptor; `.sdf`, `.smi`, `.smiles`, `.txt` for library; `.pdb`, `.sdf` for ligand)
    - Implement `derive_target_name(pdb_filename)` that strips the `.pdb` extension and directory components
    - Implement `validate_params(cutoff, top_fraction, chunk_size)` that checks cutoff > 0, 0 < top_fraction ≤ 1.0, chunk_size ≥ 1000
    - Implement `validate_binding_site(method, fields)` that ensures the selected method has all required fields filled
    - _Requirements: 3.4, 4.6, 7.3, 7.4, 7.5, 7.6_
  - [x] 2.3 Write property test: file extension validation (Property 1)
    - **Property 1: File extension validation**
    - Use Hypothesis to generate arbitrary filenames with various extensions and verify the validator accepts if and only if the extension is in the allowed set for the given file type
    - **Validates: Requirements 3.4**
  - [x] 2.4 Write property test: target name derivation (Property 3)
    - **Property 3: Target name derivation from PDB filename**
    - Use Hypothesis to generate valid PDB filenames and verify the derived target name equals the filename stem with no directory components
    - **Validates: Requirements 7.3**
  - [x] 2.5 Write property test: parameter validation correctness (Property 4)
    - **Property 4: Parameter validation correctness**
    - Use Hypothesis to generate combinations of cutoff (float), top_fraction (float), and chunk_size (integer) and verify the validator accepts if and only if cutoff > 0, 0 < top_fraction ≤ 1.0, and chunk_size ≥ 1000
    - **Validates: Requirements 7.4, 7.5, 7.6**

- [x] 3. Implement the job store service
  - [x] 3.1 Create webapp/services/job_store.py with JobStore class
    - Implement `__init__(store_path)` that initializes the JSON file path
    - Implement `_read()` and `_write(data)` with file locking (using `fcntl.flock`) for thread safety
    - Implement `add_job(record: JobRecord)` to append a new job record
    - Implement `update_job(job_id, updates)` to update fields on an existing record
    - Implement `get_job(job_id)` to retrieve a single job by SLURM job ID
    - Implement `get_jobs_for_session(session_id)` to retrieve all jobs for a session, newest first
    - Implement `get_active_jobs()` to retrieve all PENDING or RUNNING jobs
    - _Requirements: 8.5, 9.1, 11.3_
  - [x] 3.2 Write property test: job record round-trip (Property 6)
    - **Property 6: Job record round-trip through store**
    - Use Hypothesis to generate valid JobRecord instances, write them to the store, read them back by job_id, and verify all fields are preserved
    - **Validates: Requirements 8.5**
  - [x] 3.3 Write property test: session filtering (Property 9)
    - **Property 9: Job store session filtering**
    - Use Hypothesis to generate sets of job records with various session IDs, store them, query for a specific session, and verify only matching jobs are returned
    - **Validates: Requirements 11.3**

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass with `pixi run python -m pytest webapp/tests/ -v`, ask the user if questions arise.

- [x] 5. Implement the SLURM client service
  - [x] 5.1 Create webapp/services/slurm_client.py with SlurmClient class
    - Define custom `SlurmError` exception class with command, return_code, and stderr fields
    - Implement `_run(cmd, timeout=30)` helper that calls `subprocess.run()` with capture, timeout, and error handling
    - Implement `sbatch(script_path, script_args)` that runs `sbatch <script_path> <args>` and parses the returned job ID
    - Implement `squeue(job_ids, user)` that runs `squeue --format` and parses output into list of dicts
    - Implement `sacct(job_ids)` that runs `sacct --format` and parses output into list of dicts
    - Implement `scancel(job_id)` that runs `scancel <job_id>`
    - Implement `is_available()` that checks if `squeue` is accessible
    - _Requirements: 8.2, 9.1, 15.2_
  - [x] 5.2 Write unit tests for SlurmClient with mocked subprocess
    - Test sbatch parsing of job ID from stdout
    - Test squeue output parsing into structured dicts
    - Test sacct output parsing
    - Test SlurmError raised on non-zero return code
    - Test timeout handling
    - _Requirements: 8.2, 8.4, 12.2_

- [x] 6. Implement the file upload handler service
  - [x] 6.1 Create webapp/services/file_upload.py with FileUploadHandler class
    - Implement `validate_and_save(file, session_id, file_type)` that validates extension and size, saves to `webapp/uploads/<session_id>/`, and returns the saved path
    - Implement `get_upload_dir(session_id)` that returns (and creates if needed) the session upload directory
    - Implement `cleanup_session(session_id)` to remove all uploaded files for a session
    - Use `werkzeug.utils.secure_filename` for safe filenames
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 12.1_
  - [x] 6.2 Write property test: upload path session isolation (Property 2)
    - **Property 2: Upload path follows session isolation pattern**
    - Use Hypothesis to generate session IDs and filenames, verify the upload path follows `webapp/uploads/<session_id>/<filename>` and distinct session IDs never share a directory
    - **Validates: Requirements 3.6, 11.2**

- [x] 7. Implement the job submission service
  - [x] 7.1 Create webapp/services/job_submission.py with JobSubmissionService class
    - Implement `__init__(slurm_client, job_store, project_root)` with dependency injection
    - Implement `build_command_args(params: JobParams)` that converts JobParams into CLI argument list: correct script path, PDB and library as positional args, exactly one binding site flag, all optional parameters
    - Implement `submit_standard(params)` that calls `build_command_args`, executes via `slurm_client.sbatch`, creates a JobRecord, stores it, and returns it
    - Implement `submit_large_scale(params)` that calls `build_command_args`, executes via subprocess (bash, not sbatch), parses multiple job IDs from output, creates a JobRecord with child_job_ids, stores it, and returns it
    - Implement `cancel_job(job_id, session_id)` that verifies session ownership then calls `slurm_client.scancel`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 15.2_
  - [x] 7.2 Write property test: command construction (Property 5)
    - **Property 5: Command construction from JobParams**
    - Use Hypothesis to generate valid JobParams objects and verify `build_command_args` produces a list that: starts with the correct script path, includes PDB and library as first two positional args, includes exactly one binding site flag, includes all optional parameters, and contains no `None` string values
    - **Validates: Requirements 8.1**

- [x] 8. Implement the job monitor service
  - [x] 8.1 Create webapp/services/job_monitor.py with JobMonitor class
    - Implement `__init__(slurm_client, job_store, poll_interval=30)` with dependency injection
    - Implement `start()` that launches a daemon thread running `_poll_loop()`
    - Implement `stop()` that sets a stop event and joins the thread
    - Implement `_poll_loop()` that calls `poll_once()` at the configured interval
    - Implement `poll_once()` that fetches active jobs from the store, queries SLURM via squeue/sacct, updates job statuses, sets results_path on COMPLETED, sets error_message on FAILED/TIMEOUT
    - Implement `get_job_status(job_id)` that reads from the store
    - Wrap all errors in `poll_once()` with try/except so the thread never crashes
    - _Requirements: 9.1, 9.3, 9.5, 9.6_

- [x] 9. Checkpoint — Ensure all tests pass
  - Ensure all tests pass with `pixi run python -m pytest webapp/tests/ -v`, ask the user if questions arise.

- [x] 10. Create base template and static assets
  - [x] 10.1 Create webapp/templates/base.html with Bootstrap 5 layout
    - Include Bootstrap 5 CSS and JS via CDN, Bootstrap Icons
    - Create responsive navigation bar with links to Dashboard (home), Help page
    - Add flash message rendering area
    - Add block placeholders for title, content, and extra scripts
    - Initialize Bootstrap tooltips via JS (`data-bs-toggle="tooltip"`)
    - _Requirements: 2.1, 2.2, 2.3_
  - [x] 10.2 Create webapp/static/css/style.css with custom styles
    - Style the log viewer (monospaced font, scrollable container, max-height 500px)
    - Style tooltip icons
    - Style status badges (color-coded for PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT)
    - _Requirements: 14.2_
  - [x] 10.3 Create webapp/static/js/form.js with form interactivity
    - Implement binding site selector: radio buttons that show/hide the relevant input fields (ligand upload, residue name text, XYZ coordinate fields, residue numbers + chain ID)
    - Implement screening mode toggle: show/hide chunk size, partition, and max parallel fields when Large-Scale is selected
    - Implement auto-fill target name from PDB filename on file selection
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 6.1, 6.2, 6.3, 7.3_

- [x] 11. Implement dashboard route and template
  - [x] 11.1 Create webapp/routes/dashboard.py with dashboard blueprint
    - Implement `GET /` route that renders the dashboard template with the submission form and the user's job list (from `job_store.get_jobs_for_session`)
    - _Requirements: 2.1_
  - [x] 11.2 Create webapp/templates/dashboard.html with submission form and job list
    - Build the parameter form with: PDB file upload, library file upload, binding site selector (4 radio options with conditional fields), cutoff (default 10.0), target name (auto-filled), top fraction (default 0.02), screening mode toggle (Standard/Large-Scale), large-scale fields (chunk size default 1000000, partition default ga100, max parallel default 50)
    - Add tooltip icons (`<i>` with `data-bs-toggle="tooltip"` and `data-bs-title`) next to every parameter with the tooltip text specified in the design
    - Build the job list table with columns: SLURM Job ID, Target, Library, Mode, Submitted, Status (color-coded badge)
    - Add "Submit Job" button
    - _Requirements: 2.1, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 9.2_

- [x] 12. Implement job submission route
  - [x] 12.1 Create webapp/routes/jobs.py with jobs blueprint
    - Implement `POST /jobs/submit` route: validate form inputs (file extensions, required fields, binding site, numeric params), upload files via FileUploadHandler, build JobParams, submit via JobSubmissionService, flash success message with job ID, redirect to job detail page
    - Implement `GET /jobs/<id>` route: verify session ownership, fetch job from store, render job detail template
    - Implement `POST /jobs/<id>/cancel` route: verify session ownership, call `cancel_job`, flash result, redirect to job detail
    - On validation errors, re-render the form with inline error messages and preserved field values
    - _Requirements: 3.4, 3.5, 4.6, 7.4, 7.5, 7.6, 8.1, 8.2, 8.3, 8.4, 9.4, 11.4, 12.4, 15.1, 15.2, 15.3, 15.4_
  - [x] 12.2 Create webapp/templates/job_detail.html
    - Display all submission parameters in a summary section
    - Display current status with color-coded badge
    - Show "Cancel Job" button only for PENDING/RUNNING jobs
    - Show "View Results" link when status is COMPLETED
    - Show log viewer section for RUNNING/COMPLETED/FAILED jobs
    - On FAILED/TIMEOUT, display last 50 lines of SLURM log
    - _Requirements: 9.4, 9.6, 14.1, 15.1_

- [x] 13. Implement results viewing and download routes
  - [x] 13.1 Create webapp/services/results_parser.py with results parsing and pagination
    - Implement `parse_results(results_path)` that reads `SMILES,score` lines, sorts by descending score, and returns list of `(rank, smiles, score)` tuples
    - Implement `paginate(items, page, per_page)` that returns a page slice and pagination metadata (total_pages, current_page, has_prev, has_next)
    - _Requirements: 10.1, 10.2_
  - [x] 13.2 Write property test: results file parsing (Property 7)
    - **Property 7: Results file parsing**
    - Use Hypothesis to generate lists of (SMILES, score) pairs, write them as CSV lines, parse them, and verify output is sorted by descending score, ranks are sequential from 1, and every input line is represented
    - **Validates: Requirements 10.1**
  - [x] 13.3 Write property test: pagination correctness (Property 8)
    - **Property 8: Pagination correctness**
    - Use Hypothesis to generate lists of N items and page size P > 0, verify ceil(N/P) pages, each page has at most P items, and the union of all pages equals the original list in order
    - **Validates: Requirements 10.2**
  - [x] 13.4 Create webapp/routes/results.py with results blueprint
    - Implement `GET /jobs/<id>/results` route: verify session ownership, parse results file, paginate, render results template
    - Implement `GET /jobs/<id>/results/download` route: verify session ownership, serve `results.txt` as downloadable CSV with appropriate Content-Type and Content-Disposition headers
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 11.4_
  - [x] 13.5 Create webapp/templates/results.html
    - Display summary header with total hits, target name, library name
    - Render sortable table with columns: Rank, SMILES, Score
    - Add pagination controls (previous/next, page numbers)
    - Add "Download Results" button
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [x] 14. Implement log viewing route
  - [x] 14.1 Create webapp/routes/logs.py with logs blueprint
    - Implement `GET /jobs/<id>/log` route: verify session ownership, read SLURM log file, return log content as JSON (for AJAX refresh) or render in template
    - Support a "Refresh Log" action that re-reads the log file
    - _Requirements: 14.1, 14.2, 14.3_

- [x] 15. Implement help page
  - [x] 15.1 Create webapp/routes/help.py with help blueprint
    - Implement `GET /help` route that renders the help template
    - _Requirements: 13.1_
  - [x] 15.2 Create webapp/templates/help.html
    - Section 1: Step-by-step overview of the DrugCLIP screening workflow (pocket extraction → library conversion → encoding → scoring)
    - Section 2: Description of each binding site definition method with guidance on when to use each
    - Section 3: Description of the two screening modes with guidance on which to choose based on library size
    - Section 4: FAQ or troubleshooting tips
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

- [x] 16. Implement error page template
  - [x] 16.1 Create webapp/templates/error.html
    - Display user-friendly error message with reference UUID
    - Provide a link back to the dashboard
    - _Requirements: 12.3_

- [x] 17. Checkpoint — Ensure all tests pass
  - Ensure all tests pass with `pixi run python -m pytest webapp/tests/ -v`, ask the user if questions arise.

- [x] 18. Wire everything together and write integration tests
  - [x] 18.1 Register all blueprints in app.py and verify routing
    - Import and register dashboard, jobs, results, logs, and help blueprints in `create_app()`
    - Initialize services (SlurmClient, FileUploadHandler, JobStore, JobSubmissionService, JobMonitor) and attach to app context
    - Verify all routes are accessible and return correct status codes
    - _Requirements: 1.2, 2.3_
  - [x] 18.2 Create webapp/tests/conftest.py with test fixtures
    - Create Flask test client fixture with test configuration
    - Create mock SlurmClient fixture that simulates sbatch/squeue/sacct/scancel responses
    - Create temporary job store fixture using tmp_path
    - Create session helper fixtures for multi-tenancy testing
    - _Requirements: 11.1, 11.3_
  - [x] 18.3 Write property test: session authorization enforcement (Property 10)
    - **Property 10: Session authorization enforcement**
    - Use Hypothesis to generate pairs of distinct session IDs, create a job owned by session A, and verify that session B is denied access (403) to that job's detail, results, and log, while session A is permitted
    - **Validates: Requirements 11.4**
  - [x] 18.4 Write integration tests for route handlers
    - Test dashboard rendering (GET `/` returns 200 with form and job list)
    - Test submission success flow (mock sbatch → redirect to job detail with flash)
    - Test submission failure flow (mock failed sbatch → error message)
    - Test job detail page rendering for each status
    - Test cancel button visibility (shown for PENDING/RUNNING, hidden for COMPLETED/FAILED)
    - Test results download (correct content-type and content)
    - Test help page sections (all four sections present)
    - Test error page (reference ID displayed on 500)
    - _Requirements: 2.1, 8.3, 8.4, 9.2, 9.4, 10.3, 13.1, 12.3, 15.1_

- [x] 19. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass with `pixi run python -m pytest webapp/tests/ -v`, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate the 10 universal correctness properties defined in the design document
- Unit tests validate specific examples and edge cases
- All Python commands run through `pixi run python ...` as specified by the project's Pixi setup
- The webapp delegates all heavy computation to existing shell scripts (`submit_screening.sh`, `submit_large_screening.sh`) — no reimplementation of the pipeline
