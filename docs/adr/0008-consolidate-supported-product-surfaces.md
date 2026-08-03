# Consolidate supported product surfaces around one core package

ReacNet Scope supports one Dash Web application, one `reacnet-scope` CLI, and the `reacnet_scope` Python package as its public product surfaces. Business logic moves behind the core package while the legacy static Web, historical CLI entry points, and `rng_tools` public package are removed; accepting the breaking cleanup at the current pre-1.0 stage avoids maintaining divergent implementations and lets macOS/Windows local workstations and Linux servers share the same contracts.
