// Package adminclient is a thin HTTP client for the Jentic admin config surface:
// runtime, DB-backed credential provider configuration. It wraps the shared
// httpx transport and exposes the three /admin/config/providers endpoints.
package adminclient

import (
	"context"
	"net/http"
	"time"

	"github.com/jentic/jentic-one/cli/internal/httpx"
)

// Client talks to a single Jentic control-plane base URL.
type Client struct {
	http *httpx.Client
}

// New returns a client for the given base URL (trailing slash trimmed).
func New(baseURL string) *Client {
	return &Client{http: httpx.New(baseURL, 15*time.Second)}
}

// HTTPError is the shared problem-details transport error.
type HTTPError = httpx.HTTPError

// ProviderConfig is a stored provider config with secret fields redacted.
type ProviderConfig struct {
	Name      string         `json:"name"`
	Config    map[string]any `json:"config"`
	CreatedAt string         `json:"created_at"`
	UpdatedAt string         `json:"updated_at,omitempty"`
}

// providerList mirrors the list response envelope.
type providerList struct {
	Data []ProviderConfig `json:"data"`
}

// setRequest is the PUT body: provider-specific fields validated server-side.
type setRequest struct {
	Config map[string]any `json:"config"`
}

// SetProvider creates or updates a provider config by name. The config map
// carries provider-specific fields (e.g. project_id, client_id, client_secret);
// the server validates them by name, encrypts secrets, and redacts them on the
// returned record.
func (c *Client) SetProvider(ctx context.Context, token, name string, config map[string]any) (*ProviderConfig, error) {
	var out ProviderConfig
	body := setRequest{Config: config}
	if err := c.http.Do(ctx, http.MethodPut, "/admin/config/providers/"+name, token, body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetProvider returns a stored provider config by name (secrets redacted).
func (c *Client) GetProvider(ctx context.Context, token, name string) (*ProviderConfig, error) {
	var out ProviderConfig
	if err := c.http.Do(ctx, http.MethodGet, "/admin/config/providers/"+name, token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ListProviders returns all stored provider configs (secrets redacted).
func (c *Client) ListProviders(ctx context.Context, token string) ([]ProviderConfig, error) {
	var out providerList
	if err := c.http.Do(ctx, http.MethodGet, "/admin/config/providers", token, nil, &out); err != nil {
		return nil, err
	}
	return out.Data, nil
}
