PY := python3

.PHONY: ingest ingest-fixture backtest validate paper-dry-run test clean

ingest:
	$(PY) -m daybreak.data.ingest

ingest-fixture:
	$(PY) -m daybreak.data.ingest --fixture

backtest:
	$(PY) -m daybreak.backtest.run

validate:
	$(PY) -m daybreak.validate.harness

paper-dry-run:
	$(PY) -m daybreak.execute.runner --dry-run

test:
	$(PY) -m pytest tests/ -q

clean:
	rm -rf pit_data reports __pycache__ .pytest_cache
