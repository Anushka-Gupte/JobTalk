import json

import pytest
from jobtalk.extract import extract_job
from jobtalk.models import Job


def fake_chat(payload):
    return lambda system, user, settings: payload


def test_extract_parses_model_json():
    raw = json.dumps({"customer": "Sam", "items": [
        {"description": "Labor", "kind": "labor", "qty": "2", "unit_price": "85"}]})
    job = extract_job("x", {}, chat=fake_chat(raw))
    assert job.customer == "Sam" and job.items[0].qty == 2.0 and job.items[0].unit_price == 85.0


def test_messy_model_output_is_tolerated():
    job = Job.from_dict({"items": [{"description": "Pipe", "qty": "lots", "unit_price": None}, "junk", {}]})
    assert len(job.items) == 1 and job.items[0].qty == 1.0 and job.items[0].unit_price is None


def test_invalid_json_raises_clear_error():
    with pytest.raises(RuntimeError, match="valid JSON"):
        extract_job("x", {}, chat=fake_chat("not json"))


def test_unreachable_llm_raises_clear_error():
    def boom(*a):
        raise OSError("down")
    with pytest.raises(RuntimeError, match="Ollama"):
        extract_job("x", {}, chat=boom)
