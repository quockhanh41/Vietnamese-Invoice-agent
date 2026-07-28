"""Deterministic domain rules — pure functions, zero I/O.

This package is where flags come from. CLAUDE.md's safety property says the LLM
never raises a flag; these modules are the other half of that sentence. Nothing
here touches a database, a clock, the network or an LLM: everything arrives as
an argument and leaves as a return value.
"""
