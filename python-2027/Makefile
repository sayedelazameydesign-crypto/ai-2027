.PHONY: setup lint typecheck test demo providers live bench audit gateway-test studio agent check

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip uv
	.venv/bin/uv pip install --python .venv/bin/python -e . --group dev

lint:
	.venv/bin/ruff check src tests workers/python-worker/src
	.venv/bin/ruff format --check src tests workers/python-worker/src

typecheck:
	.venv/bin/mypy src tests

test:
	.venv/bin/pytest --cov-fail-under=90

gateway-test:
	node --test workers/edge-gateway/tests/gateway.test.mjs

demo:
	.venv/bin/ai2027-py27 demo

studio:
	.venv/bin/ai2027-py27 studio --host 0.0.0.0 --port 8080

agent:
	.venv/bin/ai2027-py27 agent "Inspect python-2027 and generate a verified HTML + SVG architecture report"

providers:
	.venv/bin/ai2027-py27 providers

live:
	.venv/bin/ai2027-py27 live --provider all

bench:
	.venv/bin/ai2027-py27 bench --iterations 25

audit:
	.venv/bin/ai2027-py27 audit python-2027 connectors-hub projects/agi-system projects/1pro projects/open_source-

check: lint typecheck test gateway-test demo providers
