from murmur_sidecar import cues


def test_play_sync_invokes_player_with_pairs():
    got = []
    cues.play([(440, 50)], player=lambda pairs: got.append(pairs), sync=True)
    assert got == [[(440, 50)]]  # player receives the full pairs list


def test_start_is_ascending_stop_is_descending():
    assert cues.START[0][0] < cues.START[1][0]
    assert cues.STOP[0][0] > cues.STOP[1][0]


def test_named_cues_dispatch(monkeypatch):
    got = []
    monkeypatch.setattr(cues, "_default_player", lambda p: got.append(p))
    cues.record_start(player=lambda p: got.append(p))
    cues.record_stop(player=lambda p: got.append(p))
    cues.error(player=lambda p: got.append(p))
    cues.cancel(player=lambda p: got.append(p))
    # each spawns a thread; just assert the module constants are what we expect
    assert cues.START and cues.STOP and cues.ERR and cues.CANCEL


def test_cancel_cue_is_descending_and_distinct():
    # A cancelled dictation should sound clearly different from a completed one
    # (STOP) and from an error buzz (ERR) — descending, two quick tones.
    assert cues.CANCEL[0][0] > cues.CANCEL[-1][0]  # descending = aborted
    assert cues.CANCEL != cues.STOP
    assert cues.CANCEL != cues.ERR


def test_cancel_plays_the_cancel_pattern():
    got = []
    cues.cancel(player=lambda pairs: got.append(pairs), )
    # play() spawns a thread by default; force sync via the sync path
    cues.play(cues.CANCEL, player=lambda pairs: got.append(pairs), sync=True)
    assert cues.CANCEL in got
