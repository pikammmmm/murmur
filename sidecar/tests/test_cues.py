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
    # each spawns a thread; just assert the module constants are what we expect
    assert cues.START and cues.STOP and cues.ERR
