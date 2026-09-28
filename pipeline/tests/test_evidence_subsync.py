"""Tests for subtitle synchronization against a speech reference."""

from __future__ import annotations

import random

import pytest

from pipeline.evidence import subsync
from pipeline.evidence.opensubtitles import Candidate, rank_candidates


def _speech(duration: float, seed: int = 3) -> list[subsync.Cue]:
    rng = random.Random(seed)
    cues, t = [], 5.0
    while t < duration - 10:
        length = rng.uniform(0.6, 3.5)
        cues.append((t, t + length, "words"))
        t += length + rng.uniform(0.4, 6.0)
    return cues


def test_align_recovers_offset_and_framerate():
    reference = _speech(3000.0)
    # Subtitle authored for a 25 fps (PAL) release: times compressed by 23.976/25, then shifted.
    scale = 23.976 / 25
    shifted = [((s - 7.3) * scale, (e - 7.3) * scale, t) for s, e, t in reference if s > 10]
    result = subsync.align(reference, shifted, 3000.0)
    assert result.scale == pytest.approx(25 / 23.976, rel=1e-4)
    synced = subsync.apply(shifted, result)
    errors = [abs(a[0] - b[0]) for a, b in zip(synced, [c for c in reference if c[0] > 10])]
    assert sorted(errors)[len(errors) // 2] < 0.25
    assert result.lift > 1.5 and result.prominence > 5


def test_align_follows_a_re_edit_with_windowed_shift():
    reference = _speech(2400.0, seed=9)
    # The second half of this release is 3 s late (an extra shot inserted).
    subtitle = [(s + (3.0 if s > 1200 else 0.0), e + (3.0 if s > 1200 else 0.0), t) for s, e, t in reference]
    result = subsync.align(reference, subtitle, 2400.0)
    synced = subsync.apply(subtitle, result)
    late = [abs(a[0] - b[0]) for a, b in zip(synced, reference) if b[0] > 1500]
    assert sorted(late)[len(late) // 2] < 0.3


def test_text_agreement_and_ascii_detection():
    reference = [(1.0, 3.0, "Then who the hell else are you talking to?"), (4.0, 6.0, "Well I'm the only one here")]
    assert subsync.text_agreement(reference, [(1.1, 2.9, "Who the hell else are you talking to?")]) > 0.5
    assert subsync.text_agreement(reference, [(1.1, 2.9, "Completely unrelated sentence entirely")]) == 0.0
    assert subsync.is_mostly_ascii(reference)
    assert not subsync.is_mostly_ascii([(0, 1, "我以前也有追的")])


def _candidate(file_id, *, imdb="tt0000001", hash_match=False, hi=False, mt=False, downloads=10, language="en"):
    return Candidate(subtitle_id=str(file_id), file_id=file_id, file_name="x.srt", release="Film.2019.1080p", language=language,
                     download_count=downloads, hearing_impaired=hi, machine_translated=mt, ai_translated=False,
                     foreign_parts_only=False, from_trusted=False, fps=23.976, moviehash_match=hash_match, imdb_id=imdb, files=1)


def test_ranking_prefers_hash_match_and_rejects_other_titles():
    ranked = rank_candidates([
        _candidate(1, downloads=100000),
        _candidate(2, hash_match=True, downloads=10),
        _candidate(3, hash_match=True, imdb="tt9999999", downloads=10 ** 6),   # hash collision, different film
        _candidate(4, mt=True, downloads=10 ** 6),
        _candidate(5, language="fr", downloads=10 ** 6),
    ], film_file="Film (2019).mkv", film_fps=23.976, imdb_id="tt0000001")
    ids = [candidate.file_id for _score, candidate in ranked]
    assert ids[0] == 2
    assert 3 not in ids and 5 not in ids
    assert ids.index(4) > ids.index(1)
