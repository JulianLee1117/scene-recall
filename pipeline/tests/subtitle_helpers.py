"""Complete timed dialogue fixture for intake integration tests."""


def full_english_srt() -> str:
    def stamp(seconds: int) -> str:
        return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02},000"

    return "".join(
        f"{cue}\n{stamp(20 + cue * 45)} --> {stamp(23 + cue * 45)}\n"
        f"You know what they said about the house. We should come back with your friend at number {cue}.\n\n"
        for cue in range(120)
    )
