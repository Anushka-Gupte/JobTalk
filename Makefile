.PHONY: install test lint demo clean

install:
	pip install -e ".[audio,dev]"

test:
	pytest

lint:
	ruff check src tests scripts

demo:
	python -m jobtalk --demo --business "Dave's Plumbing" --tax 0.08 --out output/quote.html

clean:
	rm -rf output build dist .pytest_cache .ruff_cache src/*.egg-info
