# Cancelling & Deleting Jobs

## Cancelling a Job

You can cancel a **PENDING** or **RUNNING** job from its detail page.

1. Go to the job detail page (click the job ID in the dashboard)
2. Click **Cancel Job** in the Actions panel
3. Confirm the cancellation

The job will be sent a `scancel` signal via SLURM. The status will update to **CANCELLED** on the next poll (within 30 seconds).

!!! note
    Cancelling a large-scale job also cancels all its child array jobs.

---

## Deleting a Job

Deleting removes the job record from your job list. It does **not** delete any files on disk (results, logs, uploaded PDB).

You can only delete jobs that are no longer active — status must be **COMPLETED**, **FAILED**, **CANCELLED**, or **TIMEOUT**. Cancel the job first if it is still running.

To delete:

1. Go to the dashboard
2. Click the 🗑 trash icon on the right side of the job row
3. Confirm the deletion

The job disappears from your list immediately.

---

## Recovering Results After Deletion

If you delete a job but still need its results, the output files remain on disk at:

```
jobs/<target>_vs_<library>/results.txt
```

You can re-import or access them directly on the cluster.
