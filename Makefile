.PHONY: install test run init

install:
	python3 -m pip install -e '.[dev]'

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

init:
	PYTHONPATH=src python3 -m meme_flight_recorder.cli init-db

run:
	uvicorn meme_flight_recorder.api:app --reload
