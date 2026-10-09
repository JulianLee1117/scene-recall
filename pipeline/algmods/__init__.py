"""Alg Mods: deterministic per-frame treatments (NumPy/OpenCV) for the Alg Mods Lab pilot.

Each mod is a pure function or a small stateful tracker over output-sized RGB
frames plus optional evidence (a subject mask, a motion direction). Nothing here
touches the index, the document schema or the effects pass; the offline renderer
in :mod:`pipeline.algmods.render` exists so treatments can be judged on real
library shots before any of them become an effect kind (ADR-0106).
"""
