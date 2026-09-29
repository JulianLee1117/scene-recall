"""Moment-level match cuts: any instant of any shot, matched by measured picture and motion.

``index`` compiles the per-film ``moments`` evidence into one memory-mapped
library index; ``score`` compares a cut pair (outgoing instant, incoming
instant) under an optional reframe; ``find`` retrieves and ranks incoming
instants for a reference; ``api`` serves the Lab workspace.
"""
