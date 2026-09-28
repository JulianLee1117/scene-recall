"""Heard meaning cannot be replaced by generic musical mood or stale cues."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.lab import music
from pipeline.lab.music_evidence import music_evidence
from pipeline.lab.song_meaning import SongMeaning, validate_song_meaning
from pipeline.tests.test_lab_music import _audio_interpretation


def _meaning():
    return {"vocal_status": "partly_understood", "summary": "Trying to hold a connection while anticipating its loss.",
            "themes": ["fear of separation", "resisting change"],
            "cues": [{"start": 1., "end": 3., "paraphrase": "The speaker wants the connection to continue despite expecting it to end.",
                      "confidence": "medium"}], "uncertainty": "One phrase is obscured by the accompaniment."}


def test_heard_paraphrases_use_source_time_and_reach_all_editorial_stages(config, monkeypatch):
    answer = _audio_interpretation()
    answer["song_meaning"] = _meaning()
    call = MagicMock(return_value=answer)
    monkeypatch.setattr(music, "_hosted_json", call)
    passage = {"start": 10., "end": 16.}
    analysis = music.interpret_audio(config, "song", passage, Path("supplied.wav"), "", "meaning-test")
    assert analysis["song_meaning"]["cues"][0]["start"] == 11
    assert analysis["song_meaning"]["cues"][0]["end"] == 13
    evidence = music_evidence({"track": {"id": "song"}, "passage": passage, "analysis": analysis})
    assert evidence["song_meaning"]["themes"] == ["fear of separation", "resisting change"]
    assert evidence["song_meaning"]["uncertainty"] == _meaning()["uncertainty"]
    assert evidence["song_meaning"]["provenance"]["source"] == "ai-heard-paraphrase"
    assert "not verified" in evidence["song_meaning"]["note"]
    prompt = call.call_args.args[1]
    assert "DO listen to the meaning" in prompt
    assert "never infer hopeful acceptance" in prompt
    assert "song_meaning" in call.call_args.args[2]["required"]
    # Listening again must not turn its previous imagined imagery into evidence.
    assert music_evidence({"track": {"id": "song"}, "passage": passage, "analysis": analysis},
                          include_analysis=False)["song_meaning"] is None


@pytest.mark.parametrize("status", ["unclear", "no_vocals"])
def test_unclear_vocals_cannot_establish_lyric_themes(status):
    value = _meaning()
    value["vocal_status"] = status
    with pytest.raises(ValueError, match="cannot establish"):
        SongMeaning.model_validate(value)
    value.update(themes=[], cues=[], summary="Vocal words could not be established.")
    assert SongMeaning.model_validate(value).themes == []


def test_clear_meaning_requires_an_audible_cue_and_all_cues_stay_in_passage():
    value = _meaning()
    value["cues"] = []
    with pytest.raises(ValueError, match="heard paraphrase"):
        SongMeaning.model_validate(value)
    with pytest.raises(ValueError, match="inside the selected passage"):
        validate_song_meaning(_meaning(), {"start": 2, "end": 6})
    with pytest.raises(ValueError, match="positive duration"):
        value = _meaning()
        value["cues"][0]["end"] = 1
        SongMeaning.model_validate(value)


@pytest.mark.parametrize("mutation", [
    lambda a: a["song_meaning_provenance"].update(track="other"),
    lambda a: a["song_meaning_provenance"].update(passage={"start": 0, "end": 10}),
    lambda a: a["song_meaning_provenance"].update(source="unscoped"),
    lambda a: a["provenance"].update(track="other"),
    lambda a: a["song_meaning"]["cues"][0].update(end=100),
    lambda a: a["song_meaning"].update(vocal_status="unclear"),
])
def test_stale_or_invalid_meaning_never_enters_shared_editorial_evidence(mutation):
    provenance = {"track": "song", "passage": {"start": 0, "end": 6}}
    analysis = {**_audio_interpretation(), "provenance": deepcopy(provenance), "song_meaning": _meaning(),
                "song_meaning_provenance": {**deepcopy(provenance), "source": "ai-heard-paraphrase"}}
    document = {"track": {"id": "song"}, "passage": provenance["passage"], "analysis": analysis}
    assert music_evidence(document)["song_meaning"] is not None
    mutation(analysis)
    assert music_evidence(document)["song_meaning"] is None


def test_legacy_analysis_does_not_gain_invented_meaning():
    analysis = _audio_interpretation()
    del analysis["song_meaning"]
    assert music.validate_interpretation(analysis, {"start": 0, "end": 6}).song_meaning is None
    with pytest.raises(ValueError):
        music.AudioInterpretation.model_validate(analysis)
