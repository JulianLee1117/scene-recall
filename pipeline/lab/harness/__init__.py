"""Editor harness v2: measured music, evidence-backed footage pools and a deterministic assembly.

The v1 chain asks language models for numeric cut times and picks footage from
text alone. Harness v2 keeps models for meaning (listening, concept, critique)
and makes the edit itself a measured optimization:

- ``music_map``: beats, bars, accents and loudness measured from the audio.
- ``pools``: per-act candidate footage from search v2 with shot evidence
  (action peak, measured camera, subject layout, fame/craft, scenes).
- ``assemble``: a beam search over the beat grid that chooses cuts, shots and
  source windows together.
"""
