# Prefer complete native timed evidence

When a dataset contains a valid, complete native timed evidence source, ReacNet Scope uses it instead of legacy CSV evidence. Legacy CSV remains the fallback only when the native source is absent; an existing but incomplete, corrupt, disabled, or schema-incompatible native source fails closed so evidence from different ReacNetGenerator runs is not silently mixed. A future explicit legacy-source override may bypass this guard.
