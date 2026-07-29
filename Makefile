PY := python3

.PHONY: ingest ingest-fast ingest-fixture sectors backtest validate paper-dry-run test clean

ingest:
	$(PY) -m daybreak.data.ingest

ingest-fast:
	$(PY) -m daybreak.data.ingest --skip-fundamentals

ingest-fixture:
	$(PY) -m daybreak.data.ingest --fixture

sectors:
	$(PY) -m daybreak.data.sources.yfin

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
