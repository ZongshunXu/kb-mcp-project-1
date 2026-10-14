PYTHON ?= python

.PHONY: index eval check test demo agent
index:
	$(PYTHON) -m scripts.index
eval:
	$(PYTHON) -m scripts.eval
check:
	$(PYTHON) -m scripts.check_mcp
test:
	$(PYTHON) -m pytest -q
demo:
	$(PYTHON) -m scripts.demo_server --check
agent:
	$(PYTHON) -m scripts.agent
