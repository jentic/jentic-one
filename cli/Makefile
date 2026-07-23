BINARY_CTL := jenticctl
BINARY_API := jentic
VERSION ?= dev
COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo none)
DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
PKG := github.com/jentic/jentic-one/cli/internal/cmd
LDFLAGS := -s -w \
	-X $(PKG).version=$(VERSION) \
	-X $(PKG).commit=$(COMMIT) \
	-X $(PKG).date=$(DATE)

GOBIN := $(shell go env GOPATH)/bin
GOLANGCI := $(GOBIN)/golangci-lint
GOLANGCI_VERSION := v2.12.2

.PHONY: help build build-ctl build-api test test-race cov fmt vet lint lint-fix tools tidy check clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

build: build-ctl build-api ## Build both binaries (jenticctl + jentic)

build-ctl: ## Build the jenticctl binary (stamps version/commit/date)
	go build -trimpath -ldflags "$(LDFLAGS)" -o $(BINARY_CTL) ./cmd/jenticctl

build-api: ## Build the jentic binary (stamps version/commit/date)
	go build -trimpath -ldflags "$(LDFLAGS)" -o $(BINARY_API) ./cmd/jentic

test: ## Run tests
	go test ./...

test-race: ## Run tests with the race detector
	go test -race ./...

cov: ## Run tests with a coverage report
	go test -coverprofile=coverage.out ./...
	go tool cover -func=coverage.out | tail -1

fmt: ## Format Go source
	gofmt -w .

vet: ## Run go vet
	go vet ./...

tools: ## Install pinned dev tools (golangci-lint)
	@command -v $(GOLANGCI) >/dev/null 2>&1 || \
		go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@$(GOLANGCI_VERSION)

lint: tools ## Run golangci-lint
	$(GOLANGCI) run ./...

lint-fix: tools ## Run golangci-lint with autofix
	$(GOLANGCI) run --fix ./...

tidy: ## Verify go.mod/go.sum are tidy
	go mod tidy
	git diff --exit-code go.mod go.sum

check: lint vet test-race ## Lint, vet, and race-test everything

clean: ## Remove build and coverage artifacts
	rm -f $(BINARY_CTL) $(BINARY_API) coverage.out
