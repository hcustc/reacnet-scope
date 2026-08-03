# Exclude Route artifacts from event evidence

Reaction Occurrences and Event Paths use a complete native `.timeline.h5` or, only when it is absent, the compatible `.reactionevent.csv` plus `.molecules.csv` evidence pair. A `.route` artifact is not an event-evidence fallback because its atom-transition candidates do not carry the required occurrence identity and molecular-evidence guarantees; Route indexes, preparation modes, and Dash fallbacks are removed, and any future Route analysis must be designed as a separate capability.
