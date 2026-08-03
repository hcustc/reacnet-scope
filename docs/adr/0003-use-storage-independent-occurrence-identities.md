# Use storage-independent occurrence identities

Reaction occurrence identities are derived from the transition, canonical reaction type, sorted participating atom IDs, and a deterministic duplicate ordinal when participants are unresolved. They do not include CSV row numbers or HDF5 dictionary and layout IDs; event index schema is therefore bumped and existing indexes must be rebuilt so the same resolved occurrence keeps its identity when timed evidence moves between legacy CSV and native HDF5.
