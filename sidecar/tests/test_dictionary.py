from murmur_sidecar.dictionary import build_stt_prompt, protect_clause


def test_build_stt_prompt_lists_all_terms():
    s = build_stt_prompt(["glassbar", "Rojo", "Luau"])
    assert "glassbar" in s and "Rojo" in s and "Luau" in s


def test_build_stt_prompt_empty():
    assert build_stt_prompt([]) == ""
    assert build_stt_prompt(None) == ""


def test_protect_clause_lists_terms():
    clause = protect_clause(["Rojo", "Luau"])
    assert "Rojo" in clause and "Luau" in clause


def test_protect_clause_empty():
    assert protect_clause([]) == ""


def test_blanks_are_stripped():
    assert build_stt_prompt(["  ", "Rojo", ""]) == "Vocabulary: Rojo."
