from scripts.eval_audio import compute_wer, evaluate_trade_terms, run_audio_eval


def test_compute_wer_exact_match():
    ref = "replaced forty gallon water heater"
    hyp = "replaced forty gallon water heater"
    assert compute_wer(ref, hyp) == 0.0


def test_compute_wer_with_errors():
    ref = "replaced forty gallon water heater"
    hyp = "replaced fifty gallon heater"
    # 1 sub (fifty vs forty), 1 del (water) -> 2 errors out of 5 words
    wer = compute_wer(ref, hyp)
    assert abs(wer - 0.4) < 1e-4


def test_compute_wer_empty():
    assert compute_wer("", "") == 0.0
    assert compute_wer("a", "") == 1.0
    assert compute_wer("", "a") == 1.0


def test_evaluate_trade_terms():
    ref = "Installed a P-trap, 20-amp GFCI outlet, and Romex cable."
    # Hyp missing Romex
    hyp = "Installed a P-trap and 20-amp GFCI outlet."
    res = evaluate_trade_terms(ref, hyp)
    assert res["expected_count"] == 3  # p-trap, gfci, romex
    assert res["found_count"] == 2
    assert abs(res["recall"] - 66.7) < 0.1


def test_run_audio_eval_empty_directory(tmp_path):
    res = run_audio_eval(tmp_path)
    assert res == []
