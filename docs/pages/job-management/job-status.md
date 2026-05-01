# Job Status

## Status Values

| Status | Meaning |
|--------|---------|
| **PENDING** | Job is queued in SLURM, waiting for a GPU node |
| **RUNNING** | Job is actively executing on the cluster |
| **COMPLETED** | Job finished successfully — results are available |
| **FAILED** | Job exited with an error — check the log for details |
| **CANCELLED** | Job was cancelled by the user |
| **TIMEOUT** | Job exceeded the wall-time limit |

## Monitoring

The webapp polls SLURM every 30 seconds and updates job statuses automatically. Refresh the dashboard or job detail page to see the latest status.

## Job Detail Page

Click any job ID in the dashboard table to open the job detail page. It shows:

- All submission parameters (receptor, library, binding site, cutoff, etc.)
- Current status with timestamps
- SLURM log viewer (click **Refresh Log** to load the latest output)
- Error details for FAILED/TIMEOUT jobs (last 50 lines of the log)
- Action buttons (View Results, Download Receptor PDBQT, Cancel, Delete)

## Log Viewer

The log viewer shows the raw SLURM output from the screening pipeline. Useful for:

- Checking which step the job is currently on
- Diagnosing failures (look for `Error:` or `Traceback` lines)
- Confirming the pocket was extracted correctly (residue count and centre coordinates are printed)
