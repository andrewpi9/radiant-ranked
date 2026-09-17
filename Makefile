PYTHON ?= python3

.PHONY: install ingest fresh validate rank rank-check rank-verify site serve test clean all

install:
	$(PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt

## Pull professors; resumes from the checkpoint if a previous run died.
ingest:
	$(PYTHON) scripts/ingest_rmp.py

## Ignore the checkpoint and re-pull everything (backs up the old files first).
fresh:
	$(PYTHON) scripts/ingest_rmp.py --fresh

## Re-run validation against the existing data/professors_raw.json.
validate:
	$(PYTHON) scripts/ingest_rmp.py --validate-only

## Rank the ingested professors (simulated-Elo) -> data/rankings.json
rank:
	$(PYTHON) scripts/rank_professors.py

## Rank, and verify the board is not an artifact of the RNG seed (~2x slower).
rank-check:
	$(PYTHON) scripts/rank_professors.py --stability-check

## Rank, then check the simulation against the closed-form win probability.
rank-verify:
	$(PYTHON) scripts/rank_professors.py --verify

## Run the test suite.
test:
	$(PYTHON) -m pytest -q

## Build the leaderboard page -> web/index.html (data embedded, no server needed)
site:
	$(PYTHON) scripts/build_site.py

## Serve the built site at http://localhost:8000
serve: site
	@echo "http://localhost:8000/web/"
	$(PYTHON) -m http.server 8000

## Full refresh: re-pull everything, re-rank, rebuild the page.
all: fresh rank site

## Drop the append log and checkpoint, keeping professors_raw.json.
clean:
	rm -f data/professors_raw.jsonl data/ingest_checkpoint.json
