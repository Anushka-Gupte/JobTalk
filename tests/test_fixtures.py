import json
from pathlib import Path

import pytest
from jobtalk.models import Job

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_files():
    files = sorted(FIXTURES_DIR.glob("*.json"))
    assert len(files) == 10, f"Expected 10 fixtures, found {len(files)}"
    return files


def test_all_fixtures_have_valid_schema(fixture_files):
    for f in fixture_files:
        data = json.loads(f.read_text())
        assert "id" in data
        assert data.get("trade") in {"plumbing", "electrical", "hvac"}
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
