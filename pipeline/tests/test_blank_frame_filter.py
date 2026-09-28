"""Whole-frame emptiness cannot be inferred from a dark display or background."""
from unittest.mock import patch

import pytest

from pipeline.search.retrieve import _is_unrequested_junk, search
from pipeline.tests.test_retrieve import _basis_vec, _fake_vec, _make_hybrid_mock_db, _make_unit_row


MATRIX_CAPTION = (
    'Close-up of a dark computer monitor displaying the green text “Wake up, Neo...” '
    'against a black screen, with a dim greenish cast and an ominous, mysterious atmosphere.'
)


@pytest.mark.parametrize("caption", [
    MATRIX_CAPTION,
    "A black screen displaying green text that reads Wake up, Neo.",
    "A white screen with handwritten words and a sketch.",
    "A woman writes on a whiteboard beside a white screen.",
    "A black frame surrounds a white sheet of paper bearing a message.",
    "A red capsule rests on a palm against a black background.",
    "Abstract blurred shafts of green, teal, and yellow light stream diagonally through a predominantly black frame, creating a dark, atmospheric, almost underwater or mechanical visual with no discernible figures or setting.",
    "Abstract darkness filled with drifting gray-green smoke or fog, with no discernible figures or location; the nearly black frame and diffuse low-key lighting create an ominous, obscured atmosphere.",
    "Abstract nighttime exterior with an out-of-focus horizontal beam or row of cool blue-white lights crossing a nearly black frame beneath shadowy foliage, creating a hazy, dreamlike composition.",
    "A couple embrace before the picture fades to black.",
    "A black screen with no visible detail except a small bright silhouette.",
    "A black screen with no imagery, only the words Wake up, Neo.",
    "A black screen with no visible detail. A face appears as the light changes.",
    "A monitor displays dense credit rows while a woman takes notes.",
    "Close-up of a printed cast list with dense rows of white serif credits on black paper.",
    "A man reads centered title text on a book cover; the table has an opening-card composition.",
])
def test_described_content_and_ambiguous_darkness_remain_eligible(caption):
    assert not _is_unrequested_junk({"caption": caption}, "visible words")


@pytest.mark.parametrize("caption", [
    "Black screen.",
    "A blank frame.",
    "An entirely white image.",
    "A completely black frame with no discernible setting, subjects, lighting, or visual detail.",
    "A uniformly white frame without any visible detail.",
    "A solid black screen containing no imagery.",
    "A completely black frame with no visible subjects, setting, lighting details, or discernible action.",
    "A completely black frame with no visible setting, subjects, lighting, or action, creating an empty and ambiguous visual field.",
    "A completely black frame with no visible setting, subjects, lighting details, or action; the image reads as a fade-out or blank transitional shot.",
    "Nearly black frames with no discernible setting, subjects, lighting, or action; the image appears to be a full fade-out or blackout.",
    "A nearly or completely black frame with no discernible setting, subjects, lighting details, or action.",
    "Nearly complete black frame with no discernible setting, subjects, lighting, or visual action; an empty, highly minimal image.",
])
def test_actual_whole_frame_emptiness_is_filtered_with_explicit_and_negated_overrides(caption):
    row = {"caption": caption}
    assert _is_unrequested_junk(row, "people talking")
    assert not _is_unrequested_junk(row, "a blank screen")
    assert _is_unrequested_junk(row, "people talking without blank screens")
    assert _is_unrequested_junk(row, "no black screens")


def test_blank_evidence_still_comes_only_from_visual_caption():
    assert not _is_unrequested_junk({"caption": "A man speaks in a room.",
        "dialogue": "A completely black frame with no visible detail.",
        "searchable_text": "Black screen."}, "a man")


@pytest.mark.parametrize(("caption", "override"), [
    ("Rolling end credits over a black screen.", "end credits"),
    ("A production company logo appears on a white screen.", "studio logo"),
    ("A title card displays white lettering against a black screen.", "title card"),
    ("A frozen frame shows a person against a nearly black screen.", "freeze frame"),
])
def test_other_junk_categories_and_their_overrides_remain_independent(caption, override):
    row = {"caption": caption}
    assert _is_unrequested_junk(row, "green letters")
    assert _is_unrequested_junk(row, "a black screen")
    assert not _is_unrequested_junk(row, override)


@pytest.mark.parametrize(("caption", "explicit", "negated"), [
    ("Static black screen filled with dense rows of small white serif credits, presenting a long cast list in centered text columns.",
     "cast list", "without credits"),
    ("Centered white Japanese title text appears on a completely black screen, creating a stark, minimalist opening-card composition with no visible setting or lighting beyond the text.",
     "title card", "without title cards"),
])
def test_explicit_visual_typography_keeps_correct_category_when_blank_detection_is_narrowed(caption, explicit, negated):
    row = {"caption": caption}
    assert _is_unrequested_junk(row, "romantic scene")
    assert _is_unrequested_junk(row, "black screen")
    assert _is_unrequested_junk(row, negated)
    assert not _is_unrequested_junk(row, explicit)


def test_shared_hybrid_search_keeps_real_monitor_text_but_removes_blank_and_credits(config):
    rows = [
        _make_unit_row("blank", "film-blank", caption="A black screen.", img_vec=_basis_vec(0), _distance=0.01),
        _make_unit_row("monitor", "film-monitor", caption=MATRIX_CAPTION,
                       searchable_text="Wake up Neo green text computer monitor", img_vec=_basis_vec(1), _distance=0.02),
        _make_unit_row("credits", "film-credits", caption="Rolling end credits over a black screen.",
                       img_vec=_basis_vec(2), _distance=0.03),
    ]
    db = _make_hybrid_mock_db(image_rows=rows, text_rows=rows, lexical_rows=rows)
    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search('"Wake up, Neo..." on a computer screen', db, config)
    assert [row["unit_id"] for row in results] == ["monitor"]
    assert results[0]["caption"] == MATRIX_CAPTION
