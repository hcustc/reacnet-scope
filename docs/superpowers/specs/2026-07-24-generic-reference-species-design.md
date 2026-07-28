# Generic Reference Species Design

## Goal

Make the C/O/Cl composition page usable for arbitrary reaction systems without
guessing or hard-coding a parent molecule.

## User-facing behavior

- Rename “母体” to “参考物种”.
- Let the user optionally enter an exact reference-species SMILES.
- With no reference SMILES, plot only carbon-number totals.
- With a reference SMILES, additionally plot:
  - the exact reference-species abundance;
  - all other species with the same carbon count.
- Derive the displayed formula and carbon count from the selected SMILES.
- Let the user enter the simulation timestep in ps; never silently substitute a
  different value.

## Data flow

The composition index remains compact. It stores timestep offsets and aggregate
C/O/Cl counts but no longer chooses a parent while building. For an optional
reference species, the online query reads only the already sampled timestep
lines through their indexed byte offsets. The exact-SMILES series is cached in
memory using the source signature, SMILES, and sampled timestep tuple.

This avoids both a complete `.species` rescan and a potentially huge
species-by-timestep SQLite table.

## Related correctness fixes

- Parse real `.moname` bonds as semicolon-separated comma triples such as
  `102,12203,1`.
- Preserve an explicitly entered mass tolerance of `0`; use `0.5` only when the
  input is absent.

## Validation

- Regression tests cover an abundant non-reference C1 species, optional and
  explicit reference selection, non-default timestep conversion, real
  `.moname` bond syntax, and zero mass tolerance.
- Run focused tests first, then the complete pytest suite, `compileall`, and
  `git diff --check`.
