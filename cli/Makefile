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

# Pin the generator via `go run ...@version` (never `go install`) so the global
# toolchain is untouched and the version is reproducible on every machine/CI.
# Verified against both real specs on 2026-08-04 (see impl/2.1 §2 spike note).
OAPI_CODEGEN := go run github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.8.0

# Pinned like OAPI_CODEGEN (go run ...@version, no global install) for the same
# reproducibility reasons. NB the module path stays github.com/atombender/... —
# the repo moved to omissis/go-jsonschema but kept the old import path (impl/6.0 §2).
GO_JSONSCHEMA := go run github.com/atombender/go-jsonschema@v0.23.0

.PHONY: help build build-ctl build-api test test-race test-arch test-golden golden-update cov fmt vet lint lint-fix tools tidy check clean generate-api check-cli-gen generate-config check-ctl-gen

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

test-arch: ## Run architectural guardrail tests (SDK boundary, fencing, prints, mutator lock, stdout/stderr, spec parity)
	go test ./tests/arch/...

test-golden: ## Run V1 characterization (golden) tests — the frozen shipped-CLI contract
	go test ./tests/golden/...

golden-update: ## Regenerate the V1 golden files (only when a change is authorized by a BC number)
	go test ./tests/golden/... -update

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

generate-api: ## Regenerate the typed OpenAPI clients (+ spec companions) from the vendored specs
	@echo "Vendoring specs into the module (go:embed cannot cross module roots, impl/2.1 §3a)..."
	cp ../openapi/control/control.openapi.yaml client/generated/control/spec.yaml
	cp ../openapi/broker/broker.openapi.yaml   client/generated/broker/spec.yaml
	@echo "Generating Control API client..."
	$(OAPI_CODEGEN) -config client/generated/control/cfg.yaml client/generated/control/spec.yaml
	@echo "Generating Broker API client..."
	$(OAPI_CODEGEN) -config client/generated/broker/cfg.yaml client/generated/broker/spec.yaml
	@echo "Generating spec companions (RequiredFields, sensitive-field table, op index)..."
	go run ./tools/specgen control client/generated/control
	go run ./tools/specgen broker client/generated/broker
	@echo "API code generation complete."

check-cli-gen: generate-api ## CI drift gate: fail if generated code is stale vs. the specs
	git diff --exit-code -- client/generated/

generate-config: ## Regenerate the backend config struct from the vendored config schema (impl/6.0 §2)
	@echo "Vendoring the config schema into the module (go:embed cannot cross module roots, impl/6.0 §1)..."
	cp ../config/config-schema.json internal/cli/ctl/assets/config-schema.json
	@echo "Generating the backend config struct..."
	$(GO_JSONSCHEMA) -p generated -o internal/cli/ctl/generated/config.go internal/cli/ctl/assets/config-schema.json
	@echo "Config code generation complete."

check-ctl-gen: generate-config ## CI drift gate: fail if the generated config struct is stale vs. the schema
	git diff --exit-code -- internal/cli/ctl/assets/config-schema.json internal/cli/ctl/generated/config.go

check: lint vet test-race ## Lint, vet, and race-test everything (test-race covers tests/arch + tests/golden)

clean: ## Remove build and coverage artifacts
	rm -f $(BINARY_CTL) $(BINARY_API) coverage.out
