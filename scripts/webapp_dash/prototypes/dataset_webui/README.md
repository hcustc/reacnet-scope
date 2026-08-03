# Dataset WebUI prototype

> PROTOTYPE — throwaway code. Do not promote this implementation to production.

Design question: **Can a researcher safely choose a dataset, understand independent
analysis capabilities, and manage dataset-owned preparation tasks and recoverable
workspace state?**

Three structurally different variants of the dataset experience are available on
one route and switchable with `?variant=A`, `?variant=B`, and `?variant=C`.

Run from the repository root:

```bash
python scripts/webapp_dash/prototypes/dataset_webui/serve.py
```

Then open <http://127.0.0.1:4173/?variant=A>.

Everything is simulated in browser memory. The prototype never scans datasets,
starts real indexing work, or removes files.
