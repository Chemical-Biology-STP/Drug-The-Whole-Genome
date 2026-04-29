# Requirements Document

## Introduction

A Flask web application that provides a user-friendly interface for submitting, managing, and monitoring DrugCLIP virtual screening jobs on an HPC cluster via SLURM. The application allows researchers to upload protein structures and compound libraries, configure screening parameters with guided tooltips, submit jobs to the SLURM scheduler, and view results — all without needing command-line expertise. The application supports multi-tenancy so that many concurrent users can manage their own jobs independently. All web application code resides in the `webapp/` directory at the project root.

## Glossary

- **Web_App**: The Flask web application that serves the user interface and backend API for DrugCLIP job management.
- **Job_Submission_Service**: The backend component responsible for constructing and submitting SLURM jobs to the HPC scheduler on behalf of users.
- **Job_Monitor**: The backend component responsible for polling SLURM for job status updates and making them available to users.
- **File_Upload_Handler**: The backend component responsible for receiving, validating, and storing user-uploaded files (PDB, SDF, SMI, ligand files).
- **Results_Viewer**: The UI component that displays screening results (SMILES and scores) to the user.
- **Parameter_Form**: The UI component that collects screening parameters from the user, including binding site definition, cutoff, top fraction, and screening mode.
- **Tooltip_System**: The UI component that displays plain-language explanations next to every configurable parameter and option.
- **User_Session**: A server-side session that isolates each user's uploaded files, submitted jobs, and results from other users.
- **SLURM**: The HPC workload manager used to schedule and run DrugCLIP screening jobs on GPU nodes.
- **Binding_Site_Selector**: The UI component that lets the user choose one of four methods to define the protein binding site (ligand file, HETATM residue name, XYZ coordinates, or residue numbers).
- **Screening_Mode**: Either "standard" (single SLURM job for libraries up to ~1M compounds) or "large-scale" (parallel SLURM jobs for libraries over ~1M compounds).
- **PDB**: Protein Data Bank file format containing 3D atomic coordinates of a protein structure.
- **SDF**: Structure-Data File format for representing chemical structures.
- **SMILES**: Simplified Molecular Input Line Entry System, a line notation for chemical structures.
- **Pixi**: A cross-platform package manager that manages all Python dependencies and task execution for the project via `pixi.toml` at the project root. Located at `/camp/home/yipy/.pixi/bin/pixi`. All Python commands are run through `pixi run python ...`.
- **LMDB**: Lightning Memory-Mapped Database, the internal format used by DrugCLIP for pockets and molecule libraries.

## Requirements

### Requirement 1: Project Structure

**User Story:** As a developer, I want all web application code contained in a dedicated `webapp/` directory, so that the web app is cleanly separated from the existing DrugCLIP pipeline code.

#### Acceptance Criteria

1. THE Web_App SHALL place all application source code, templates, static assets, and configuration files within the `webapp/` directory at the project root.
2. THE Web_App SHALL include a `webapp/app.py` entry point that starts the Flask development server when executed via `pixi run python webapp/app.py`.
3. THE Web_App SHALL declare all Python dependencies in the project-root `pixi.toml` file managed by Pixi, rather than maintaining a separate `requirements.txt` inside the `webapp/` directory.

### Requirement 2: User-Friendly Dashboard

**User Story:** As a researcher, I want a clean and intuitive web dashboard, so that I can submit and manage screening jobs without command-line knowledge.

#### Acceptance Criteria

1. WHEN a user navigates to the root URL, THE Web_App SHALL display a dashboard page showing a job submission form and a list of the user's previously submitted jobs.
2. THE Web_App SHALL use a responsive layout that renders correctly on screen widths from 1024 pixels to 2560 pixels.
3. THE Web_App SHALL display a navigation bar with links to the job submission form, the job list, and an about/help page.

### Requirement 3: File Upload

**User Story:** As a researcher, I want to upload my receptor PDB file and compound library file through the browser, so that I can provide inputs without using the command line.

#### Acceptance Criteria

1. THE Parameter_Form SHALL provide a file upload field for the receptor PDB file that accepts files with the `.pdb` extension.
2. THE Parameter_Form SHALL provide a file upload field for the compound library that accepts files with `.sdf`, `.smi`, `.smiles`, or `.txt` extensions.
3. WHEN a user selects the "ligand" binding site method, THE Parameter_Form SHALL provide an additional file upload field for the ligand file that accepts `.pdb` or `.sdf` extensions.
4. IF a user uploads a file with an unsupported extension, THEN THE File_Upload_Handler SHALL reject the upload and display an error message stating the accepted file types.
5. IF a user submits the form without uploading a required file, THEN THE Web_App SHALL display a validation error indicating which file is missing.
6. WHEN a file is uploaded, THE File_Upload_Handler SHALL store the file in a user-session-specific directory under `webapp/uploads/<session_id>/`.

### Requirement 4: Binding Site Definition

**User Story:** As a researcher, I want to define the protein binding site using any of the four supported methods, so that I can use whichever structural information I have available.

#### Acceptance Criteria

1. THE Binding_Site_Selector SHALL present four mutually exclusive options for defining the binding site: co-crystallized ligand file, HETATM residue name, explicit XYZ coordinates, and protein residue numbers.
2. WHEN the user selects "Co-crystallized Ligand," THE Parameter_Form SHALL display a file upload field for the ligand file and hide the other binding site input fields.
3. WHEN the user selects "HETATM Residue Name," THE Parameter_Form SHALL display a text input field for the residue name (e.g., "JHN") and hide the other binding site input fields.
4. WHEN the user selects "Explicit XYZ Coordinates," THE Parameter_Form SHALL display three numeric input fields labeled X, Y, and Z and hide the other binding site input fields.
5. WHEN the user selects "Protein Residue Numbers," THE Parameter_Form SHALL display a text input field for space-separated residue numbers and an optional text input field for the chain ID, and hide the other binding site input fields.
6. IF the user submits the form without providing any binding site definition, THEN THE Web_App SHALL display a validation error stating that a binding site definition is required.

### Requirement 5: Screening Parameters with Tooltips

**User Story:** As a researcher who may not be a computational expert, I want every parameter accompanied by a plain-language tooltip, so that I understand what each option does before I set it.

#### Acceptance Criteria

1. THE Tooltip_System SHALL display a tooltip icon next to every configurable parameter on the Parameter_Form.
2. WHEN a user hovers over or clicks a tooltip icon, THE Tooltip_System SHALL display a plain-language explanation of the parameter that a non-expert can understand.
3. THE Tooltip_System SHALL provide tooltips for the following parameters: receptor PDB file, compound library file, binding site method, cutoff radius, chain ID, target name, top fraction, screening mode, chunk size, SLURM partition, and max parallel jobs.
4. THE Tooltip_System SHALL use the following tooltip text for the "Cutoff" parameter: "How far (in Ångströms) from the binding site center to look for pocket residues. A larger value captures more of the protein around the binding site. Default is 10.0 Å, which works well for most drug-sized pockets."
5. THE Tooltip_System SHALL use the following tooltip text for the "Top Fraction" parameter: "What percentage of the screened compounds to return as hits. For example, 0.02 returns the top 2% of compounds ranked by predicted binding score. Set to 1.0 to get scores for every compound."
6. THE Tooltip_System SHALL use the following tooltip text for the "Screening Mode" parameter: "Standard mode runs everything in a single job and works for libraries up to about 1 million compounds. Large-scale mode splits the library into chunks and processes them in parallel, which is needed for bigger libraries."

### Requirement 6: Screening Mode Selection

**User Story:** As a researcher, I want to choose between standard and large-scale screening modes, so that I can efficiently screen libraries of any size.

#### Acceptance Criteria

1. THE Parameter_Form SHALL provide a toggle or radio button to select between "Standard" and "Large-Scale" screening modes.
2. WHEN the user selects "Standard" mode, THE Parameter_Form SHALL hide the chunk size, SLURM partition, and max parallel jobs fields.
3. WHEN the user selects "Large-Scale" mode, THE Parameter_Form SHALL display additional fields for chunk size (default: 1,000,000), SLURM partition (default: "ga100"), and max parallel jobs (default: 50).
4. THE Parameter_Form SHALL default to "Standard" screening mode.

### Requirement 7: Parameter Defaults and Validation

**User Story:** As a researcher, I want sensible defaults pre-filled for all optional parameters, so that I can submit a job quickly without configuring every option.

#### Acceptance Criteria

1. THE Parameter_Form SHALL pre-fill the cutoff field with a default value of 10.0.
2. THE Parameter_Form SHALL pre-fill the top fraction field with a default value of 0.02.
3. THE Parameter_Form SHALL pre-fill the target name field with the filename of the uploaded PDB file (without the `.pdb` extension) once the file is selected.
4. IF the user enters a cutoff value that is not a positive number, THEN THE Web_App SHALL display a validation error for the cutoff field.
5. IF the user enters a top fraction value that is not between 0.0 (exclusive) and 1.0 (inclusive), THEN THE Web_App SHALL display a validation error for the top fraction field.
6. IF the user enters a chunk size that is not a positive integer of at least 1000, THEN THE Web_App SHALL display a validation error for the chunk size field.

### Requirement 8: Job Submission

**User Story:** As a researcher, I want to submit a screening job with one click after filling in the form, so that the pipeline runs on the HPC without me writing any commands.

#### Acceptance Criteria

1. WHEN the user clicks the "Submit Job" button with valid inputs, THE Job_Submission_Service SHALL construct the appropriate SLURM command (`submit_screening.sh` for standard mode or `submit_large_screening.sh` for large-scale mode) with all user-specified parameters.
2. WHEN the user clicks the "Submit Job" button with valid inputs, THE Job_Submission_Service SHALL execute the SLURM submission command on the HPC and capture the returned SLURM job ID.
3. WHEN a job is submitted successfully, THE Web_App SHALL display a confirmation message containing the SLURM job ID and redirect the user to the job detail page.
4. IF the SLURM submission command fails, THEN THE Job_Submission_Service SHALL display an error message containing the failure reason from SLURM.
5. WHEN a job is submitted, THE Job_Submission_Service SHALL record the job metadata (SLURM job ID, submission timestamp, parameters, user session ID, and status) in a persistent job store.

### Requirement 9: Job Monitoring and Status

**User Story:** As a researcher, I want to see the real-time status of my submitted jobs, so that I know when results are ready or if something went wrong.

#### Acceptance Criteria

1. THE Job_Monitor SHALL poll SLURM for the status of all active jobs associated with the current user session at a configurable interval (default: 30 seconds).
2. WHEN a user views the job list page, THE Web_App SHALL display each job with its SLURM job ID, target name, library name, screening mode, submission time, and current status.
3. THE Job_Monitor SHALL recognize and display the following SLURM job states: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, and TIMEOUT.
4. WHEN a user clicks on a job in the job list, THE Web_App SHALL navigate to a job detail page showing all submission parameters, current status, and a link to the SLURM log file.
5. WHEN a job transitions to the COMPLETED state, THE Job_Monitor SHALL update the job record and make the results available for viewing.
6. WHEN a job transitions to the FAILED or TIMEOUT state, THE Job_Monitor SHALL display the last 50 lines of the SLURM log file on the job detail page.

### Requirement 10: Results Viewing and Download

**User Story:** As a researcher, I want to view and download my screening results in the browser, so that I can quickly assess hits and share them with collaborators.

#### Acceptance Criteria

1. WHEN a job has COMPLETED status, THE Results_Viewer SHALL display the results in a sortable table with columns for rank, SMILES string, and score.
2. THE Results_Viewer SHALL paginate results with 50 rows per page by default.
3. WHEN a user clicks a "Download Results" button, THE Web_App SHALL serve the full `results.txt` file as a downloadable CSV file.
4. THE Results_Viewer SHALL display a summary header showing the total number of hits, the target name, and the library name.

### Requirement 11: Multi-Tenancy and Session Isolation

**User Story:** As one of many researchers using the web app, I want my jobs and files to be isolated from other users, so that I only see my own work and cannot access others' data.

#### Acceptance Criteria

1. THE Web_App SHALL assign each browser session a unique session identifier stored in a server-side session cookie.
2. THE User_Session SHALL isolate uploaded files so that each user's files are stored in a separate directory identified by the session ID.
3. THE User_Session SHALL isolate job records so that each user can only view and manage jobs submitted from their own session.
4. IF a user attempts to access a job or file belonging to a different session, THEN THE Web_App SHALL return an HTTP 403 Forbidden response.

### Requirement 12: Error Handling and User Feedback

**User Story:** As a researcher, I want clear error messages when something goes wrong, so that I can fix the problem or seek help.

#### Acceptance Criteria

1. IF an uploaded file exceeds the maximum allowed size (500 MB), THEN THE File_Upload_Handler SHALL reject the upload and display an error message stating the size limit.
2. IF the SLURM scheduler is unreachable, THEN THE Job_Submission_Service SHALL display an error message stating that the HPC cluster is currently unavailable.
3. IF a server-side error occurs during job submission, THEN THE Web_App SHALL display a user-friendly error page with a reference ID for troubleshooting and log the full error details server-side.
4. THE Web_App SHALL display form validation errors inline next to the relevant input fields.

### Requirement 13: Help and Documentation Page

**User Story:** As a new user, I want an in-app help page explaining the screening workflow and terminology, so that I can learn how to use the tool without external documentation.

#### Acceptance Criteria

1. THE Web_App SHALL provide an accessible help page reachable from the navigation bar.
2. THE Web_App SHALL display on the help page a step-by-step overview of the DrugCLIP screening workflow (pocket extraction, library conversion, encoding, scoring).
3. THE Web_App SHALL display on the help page a description of each binding site definition method with guidance on when to use each one.
4. THE Web_App SHALL display on the help page a description of the two screening modes and guidance on which to choose based on library size.

### Requirement 14: SLURM Log Viewing

**User Story:** As a researcher, I want to view SLURM log output for my jobs in the browser, so that I can debug issues without SSH-ing into the cluster.

#### Acceptance Criteria

1. WHEN a user views the job detail page for a RUNNING or COMPLETED job, THE Web_App SHALL display a "View Log" section showing the contents of the SLURM log file.
2. THE Web_App SHALL display the log output in a scrollable, monospaced-font container with a maximum height of 500 pixels.
3. WHEN a user clicks a "Refresh Log" button, THE Web_App SHALL reload the latest contents of the SLURM log file.

### Requirement 15: Job Cancellation

**User Story:** As a researcher, I want to cancel a running or pending job from the web interface, so that I can free up cluster resources if I made a mistake.

#### Acceptance Criteria

1. WHEN a job is in PENDING or RUNNING state, THE Web_App SHALL display a "Cancel Job" button on the job detail page.
2. WHEN the user clicks "Cancel Job," THE Job_Submission_Service SHALL execute `scancel <job_id>` on the HPC to cancel the SLURM job.
3. WHEN the cancellation command succeeds, THE Job_Monitor SHALL update the job status to CANCELLED.
4. IF the cancellation command fails, THEN THE Web_App SHALL display an error message with the reason from SLURM.
