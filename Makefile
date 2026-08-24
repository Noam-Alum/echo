SHELL := /usr/bin/env bash

GOLANGCI_LINT_VERSION := v2.12.2
TOOLS_DIR := $(CURDIR)/.tools/bin
GOLANGCI_LINT := $(TOOLS_DIR)/golangci-lint
GO_FILES := $(shell find worker -type f -name '*.go' -print)

.DEFAULT_GOAL := ci

.PHONY: ci bootstrap lint typecheck test build \
	python-lint python-typecheck python-test python-build \
	go-lint go-test go-build \
	web-lint web-typecheck web-test web-build

ci:
	$(MAKE) --no-print-directory bootstrap
	$(MAKE) --no-print-directory lint
	$(MAKE) --no-print-directory typecheck
	$(MAKE) --no-print-directory test
	$(MAKE) --no-print-directory build

bootstrap:
	cd control-plane && uv sync --extra dev --locked
	cd worker && go mod download
	npm --prefix web ci

lint: python-lint go-lint web-lint

typecheck: python-typecheck web-typecheck

test: python-test go-test web-test

build: python-build go-build web-build

python-lint:
	cd control-plane && uv run --frozen ruff check .

python-typecheck:
	cd control-plane && uv run --frozen mypy echo tests

python-test:
	cd control-plane && uv run --frozen pytest

python-build:
	mkdir -p dist/python
	cd control-plane && uv build --out-dir ../dist/python

$(GOLANGCI_LINT):
	mkdir -p $(TOOLS_DIR)
	GOBIN=$(TOOLS_DIR) go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@$(GOLANGCI_LINT_VERSION)

go-lint: $(GOLANGCI_LINT)
	@unformatted="$$(gofmt -l $(GO_FILES))"; \
	if [[ -n "$$unformatted" ]]; then \
		echo "The following Go files are not formatted:"; \
		echo "$$unformatted"; \
		exit 1; \
	fi
	cd worker && $(GOLANGCI_LINT) run ./...

go-test:
	cd worker && go test ./...

go-build:
	mkdir -p dist/worker
	cd worker && go build -trimpath -o ../dist/worker/compute-worker ./cmd/compute-worker
	cd worker && go build -trimpath -o ../dist/worker/services-worker ./cmd/services-worker

web-lint:
	npm --prefix web run lint

web-typecheck:
	npm --prefix web run typecheck

web-test:
	npm --prefix web test

web-build:
	npm --prefix web run build
