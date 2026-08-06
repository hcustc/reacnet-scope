# Dataset-selection UI prototype

Throwaway UI used to answer one question:

> Does the accepted dataset-selection flow produce a clear, usable visual
> hierarchy across its canonical states at 1440px, 768px, and 320px widths?

Three structurally different variants are available on one route and are
selected with `?variant=A`, `?variant=B`, or `?variant=C`. Canonical fixture
states are selected with the on-page **PROTOTYPE 状态** control or a `?state=`
query parameter.

Run from the repository root:

```bash
python3 -m http.server 8765 --directory scripts/webapp_dash/prototypes/dataset-selection-prototype
```

Then open:

<http://127.0.0.1:8765/?variant=A&state=single-selected>

This directory is intentionally disposable. It has no production callbacks,
persistence, filesystem access, or index preparation.
