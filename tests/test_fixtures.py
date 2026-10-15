import json
from pathlib import Path

import pytest
from jobtalk.models import Job

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def train_fixture_files():
    files = sorted(FIXTURES_DIR.glob("*.json"))
    assert len(files) == 10, f"Expected 10 train fixtures, found {len(files)}"
    return files


@pytest.fixture
def holdout_fixture_files():
    holdout_dir = FIXTURES_DIR / "holdout"
    files = sorted(holdout_dir.glob("*.json"))
    assert len(files) == 8, f"Expected 8 holdout fixtures, found {len(files)}"
    return files


def _validate_fixture_file(f: Path):
    data = json.loads(f.read_text())
    assert "id" in data
    assert data.get("trade") in {"plumbing", "electrical", "hvac", "roofing", "carpentry"}
    assert len(data.get("transcript", "").strip()) > 30
    assert isinstance(data.get("rates"), dict)
    assert isinstance(data.get("expected"), dict)

    expected = data["expected"]
    assert expected.get("customer")
    assert expected.get("address")
    assert expected.get("job_summary")
    assert len(expected.get("items", [])) >= 2

    # Verify Job.from_dict succeeds on expected payload
    job = Job.from_dict(expected)
    assert len(job.items) == len(expected["items"])
    for item in job.items:
        assert item.description
        assert item.kind in {"labor", "material"}
        assert item.qty > 0


def test_train_fixtures_have_valid_schema(train_fixture_files):
    for f in train_fixture_files:
        _validate_fixture_file(f)


def test_holdout_fixtures_have_valid_schema(holdout_fixture_files):
    for f in holdout_fixture_files:
        _validate_fixture_file(f)
