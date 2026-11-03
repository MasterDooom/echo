from app.assistant.commands import Action, parse_command


def test_wake_word_and_short_command():
    assert parse_command("Echo next").action is Action.NEXT
    assert parse_command("hey Echo, pause").action is Action.PAUSE


def test_play_query_is_preserved():
    command = parse_command("Echo play Save Your Tears by The Weeknd")
    assert command.action is Action.PLAY
    assert command.query == "save your tears by the weeknd"


def test_common_playback_commands():
    assert parse_command("skip song").action is Action.NEXT
    assert parse_command("Echo play the next song").action is Action.NEXT
    assert parse_command("go back").action is Action.PREVIOUS
    assert parse_command("resume the music").action is Action.RESUME
    assert parse_command("volume up").action is Action.VOLUME_UP
    assert parse_command("mute").action is Action.MUTE


def test_unknown_and_empty_commands():
    assert parse_command("Echo").action is Action.UNKNOWN
    assert parse_command("").action is Action.UNKNOWN
    assert parse_command("open chrome").action is Action.UNKNOWN
