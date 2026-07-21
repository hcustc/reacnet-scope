"""Lightweight Dash WebUI V1 for ReacNet Scope.

This package runs in parallel with the legacy Flask-based WebUI in
``scripts.webapp``.  It only reuses existing analysis functions from
``scripts.webapp.server`` and ``rng_tools``; it does not reimplement
species lookup, transition matching, time evolution or observation
network semantics.
"""
