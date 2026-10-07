// These runnable examples are the third-party contract for the public SDK
// (impl/7.0 §4). They live in the external `client_test` package so they compile
// EXACTLY as a downstream importer would write them — no access to unexported
// helpers — and they run as part of `go test`, so the documented usage can never
// silently drift from the real API. They intentionally never call the network
// (no Output: line), so they are compile-and-construct checks, not integration
// tests.
package client_test

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/client/paginate"
)

// ExampleNewControl_injectedToken shows the file-less "bring-your-own-token"
// path: the caller already holds a bearer (e.g. an agent launched with
// JENTIC_BASE_URL + JENTIC_BEARER_TOKEN injected). No disk access, no key
// material — best for short-lived/ephemeral jobs.
func ExampleNewControl_injectedToken() {
	c, err := client.NewControl(client.Config{
		ControlBaseURL:      "https://control.jentic.example",
		InjectedBearerToken: "eyJhbGciOi...",
		SessionID:           "my-batch-job-42", // optional telemetry grouping
	})
	if err != nil {
		log.Fatal(err)
	}

	resp, err := c.ListExecutionsWithResponse(context.Background(), &control.ListExecutionsParams{})
	if err != nil {
		log.Fatal(err)
	}
	if resp.JSON200 == nil {
		log.Fatalf("unexpected status %d", resp.StatusCode())
	}
	fmt.Printf("got %d executions\n", len(resp.JSON200.Data))
}

// ExampleNewControl_identity shows the disk-backed path: given a registered
// identity + environment, the SDK uses the env-scoped Ed25519 key (under
// ~/.config/jentic/keys) plus cached access tokens (XDG state dir), performing
// the RFC 7523 OAuth exchange on demand. A custom *http.Client injects timeouts
// or a custom CA pool; the SDK wraps its transport with the retry/backoff policy.
func ExampleNewControl_identity() {
	_, err := client.NewControl(client.Config{
		ControlBaseURL:  "https://control.jentic.example",
		IdentityName:    "ci-bot",
		EnvironmentName: "prod",
		HTTPClient:      &http.Client{Timeout: 30 * time.Second},
	})
	if err != nil {
		log.Fatal(err)
	}
}

// ExampleLoadState shows reusing the CLI's own resolution: LoadState honors
// JENTIC_BASE_URL/JENTIC_BEARER_TOKEN and otherwise reads config.yaml, so an SDK
// consumer resolves connection + identity from the same source the CLI uses.
func ExampleLoadState() {
	rs, err := config.LoadState("") // "" = the active context
	if err != nil {
		log.Fatal(err)
	}
	_, err = client.NewControl(client.Config{
		ControlBaseURL:      rs.BaseURL,
		BrokerBaseURL:       rs.BrokerURL,
		IdentityName:        rs.IdentityName,
		EnvironmentName:     rs.EnvironmentName,
		InjectedBearerToken: rs.InjectedBearerToken,
		SessionID:           rs.SessionID,
	})
	if err != nil {
		log.Fatal(err)
	}
}

// ExampleAll shows draining a cursor-paginated list endpoint with the generic
// paginate.All helper: adapt one page's typed response into a paginate.Page and
// the helper walks next_cursor to completion.
func ExampleAll() {
	c, err := client.NewControl(client.Config{
		ControlBaseURL:      "https://control.jentic.example",
		InjectedBearerToken: "eyJhbGciOi...",
	})
	if err != nil {
		log.Fatal(err)
	}

	all, err := paginate.All(context.Background(), func(ctx context.Context, cursor string) (paginate.Page[control.ExecutionResponse], error) {
		params := &control.ListExecutionsParams{}
		if cursor != "" {
			params.Cursor = &cursor
		}
		resp, err := c.ListExecutionsWithResponse(ctx, params)
		if err != nil {
			return paginate.Page[control.ExecutionResponse]{}, err
		}
		if resp.JSON200 == nil {
			return paginate.Page[control.ExecutionResponse]{}, fmt.Errorf("unexpected status %d", resp.StatusCode())
		}
		next := ""
		if resp.JSON200.NextCursor != nil {
			next = *resp.JSON200.NextCursor
		}
		return paginate.Page[control.ExecutionResponse]{
			Items: resp.JSON200.Data,
			Next:  next,
		}, nil
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("drained %d executions across all pages\n", len(all))
}
