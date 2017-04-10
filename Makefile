#!/usr/bin/make
PYTHON := /usr/bin/env python
CHARM_DIR := $(PWD)
HOOKS_DIR := $(PWD)/hooks
TEST_PREFIX := PYTHONPATH=$(HOOKS_DIR)

clean:
	find . -name '*.pyc' -delete

lint:
	@tox -e pep8

unit_tests:
	@tox -e py27


bin/charm_helpers_sync.py:
	@mkdir -p bin
	@bzr cat lp:charm-helpers/tools/charm_helpers_sync/charm_helpers_sync.py \
		> bin/charm_helpers_sync.py

sync: bin/charm_helpers_sync.py
	@$(PYTHON) bin/charm_helpers_sync.py -c charm-helpers-sync.yaml
