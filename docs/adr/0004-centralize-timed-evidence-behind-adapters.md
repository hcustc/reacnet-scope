# Centralize timed evidence behind adapters

ReacNet Scope selects and reads timed evidence through one deep module with native HDF5 and legacy CSV adapters. Dataset discovery, preparation, and event indexing consume a shared selection, capability, and transition-batch interface rather than branching on artifact formats, keeping source validation, ordering, normalization, and checkpoint semantics local to the adapters.
