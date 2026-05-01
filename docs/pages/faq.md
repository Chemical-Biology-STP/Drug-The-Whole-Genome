# FAQ & Troubleshooting

## My job has been PENDING for a long time. Is something wrong?

PENDING means your job is waiting in the SLURM queue for a GPU node. During busy periods, wait times can range from minutes to hours. If the job stays PENDING for more than a few hours, the partition may be fully occupied — try again later or contact your HPC administrator.

---

## My job FAILED. How do I find out what went wrong?

Go to the job detail page and check the **SLURM Log** section. Click **Refresh Log** to load the latest output. Common causes:

- **Invalid PDB format** — ensure the file is a valid PDB with ATOM/HETATM records
- **Residue not found** — the residue number or name you specified doesn't exist in the PDB
- **Empty compound library** — the uploaded file has no valid molecules
- **GPU out of memory** — very large pockets (>511 atoms) may exceed GPU memory; try a smaller cutoff
- **Missing atoms** — incomplete structures can cause parsing errors

---

## What file formats are supported for the compound library?

`.sdf`, `.smi`, `.smiles`, `.txt` — see [Compound Libraries](getting-started/compound-libraries.md) for details.

---

## How do I choose the right cutoff radius?

The default of 10.0 Å works well for most drug-sized pockets. See [Binding Site Methods](getting-started/binding-site-methods.md#cutoff-radius) for guidance.

---

## Can I screen the same protein against multiple libraries?

Yes. Submit a separate job for each library using the same PDB and binding site definition. Each job runs independently.

---

## Why does the SDF/PDBQT download take a while?

3D conformer generation runs on the server for every hit in your results. For large result sets (thousands of compounds), this can take a minute or two. A progress modal will keep you informed — don't close the page.

---

## The 2D structures on the results page aren't showing.

The structures are rendered in the browser using RDKit.js, which is loaded from a CDN. If you are on a network without internet access, the structures will not render. The SMILES strings are still shown in the table.

---

## My session expired and I can't see my jobs.

Jobs are tied to your browser session. If you clear cookies or switch browsers, your job history will not be visible. The results files are still on disk at `jobs/<target>_vs_<library>/results.txt`.

---

## How do I add a new compound library to the server?

See [Compound Libraries — Adding New Libraries](getting-started/compound-libraries.md#adding-new-libraries-to-the-server).
