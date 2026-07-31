"""CapitalScan — Bollinger Band + Stochastic Oscillator event-study engine.

Advisory only. No execution path exists or may be added (ADR 043).

This file makes `capitalscan` a regular package rather than a namespace
package, which is what `packages = ["capitalscan"]` in the mypy config
resolves against. Without it, mypy falls back to path-based discovery and
the module-resolution ambiguity returns.
"""
