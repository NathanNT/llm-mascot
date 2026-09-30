import scoring


def test_perfect_match_and_punctuation_is_ignored():
    result = scoring.score("Ajoute la route POST /api/v2, puis valide le payload.", "ajoute la route post api v2 puis valide le payload")
    assert result.errors == 0 and result.wer == 0 and result.n_ref == 10


def test_hyphens_and_apostrophes():
    assert scoring.tokens("back-end l’ordre") == ["back", "end", "l'ordre"]
    assert scoring.score("back-end", "back end").wer == 0


def test_substitution_deletion_and_insertion_are_counted():
    result = scoring.score("deploy the api to staging", "deploy a api to the staging now")
    assert (result.substitutions, result.deletions, result.insertions) == (1, 0, 2)
    assert result.n_ref == 5 and abs(result.wer - result.errors / 5) < 1e-9
    assert "the" in result.missed


def test_missed_technical_terms_are_listed():
    result = scoring.score("configure kubernetes ingress with oauth", "configure cooper nets ingress with off")
    assert {"kubernetes", "oauth"} <= set(result.missed)


def test_accent_insensitive_mode():
    assert scoring.score("réunion prévue", "reunion prevue").wer == 1.0
    assert scoring.score("réunion prévue", "reunion prevue", strip_accents=True).wer == 0


def test_alignment_keeps_word_order_for_the_diff_view():
    ops = scoring.score("a b c", "a x c").ops
    assert [op for op, *_ in ops] == ["ok", "sub", "ok"] and ops[1][1:] == ("b", "x")


def test_empty_inputs():
    assert scoring.score("", "anything").wer == 0.0
    assert scoring.score("one two", "").wer == 1.0


def test_recommendation_prefers_the_fastest_model_close_to_the_best():
    results = [{"name": "tiny", "wer": 0.20, "seconds": 0.5}, {"name": "base", "wer": 0.06, "seconds": 1.0},
               {"name": "small", "wer": 0.05, "seconds": 3.0}, {"name": "large", "wer": 0.04, "seconds": 12.0},
               {"name": "broken", "wer": None, "seconds": None}]
    pick = scoring.recommend(results)
    assert pick == {"accuracy": "large", "speed": "tiny", "balanced": "base", "snappy": "base"}
    slow_only = scoring.recommend([{"name": "turbo", "wer": 0.1, "seconds": 7.0}])
    assert "snappy" not in slow_only and slow_only["accuracy"] == "turbo"
    assert scoring.recommend(results, snappy_seconds=0.6)["snappy"] == "tiny"
    assert scoring.recommend([]) == {}
