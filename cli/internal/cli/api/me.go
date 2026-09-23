package api

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// getMe fetches the caller's identity via GET /me and returns the AGENT variant.
//
// GET /me returns a discriminated union (MeUser | MeAgent | MeServiceAccount)
// keyed on `type`. The generated AsMeAgent() does NOT validate the discriminator
// — it would happily decode a user/service-account body into an agent-shaped
// value with empty bindings, which reads as an approved agent bound to nothing.
// So we probe the raw body's `type` first and reject a non-agent token, matching
// the guard the deleted accessclient.Me() enforced.
func (a *app) getMe(ctx context.Context) (*control.MeAgent, error) {
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	resp, err := client.GetMeWithResponse(ctx)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	// Decode straight from the raw body rather than resp.JSON200: /me is a
	// discriminated union and we need the `type` discriminator to reject a
	// non-agent token (AsMeAgent does not validate it — it would decode a
	// user/service-account into an empty-bindings agent). Reading resp.Body also
	// avoids depending on the response Content-Type (the generated typed field
	// is only populated for an application/json content type).
	var probe struct {
		Type        string          `json:"type"`
		Permissions json.RawMessage `json:"permissions"`
		Scopes      json.RawMessage `json:"scopes"`
	}
	if err := json.Unmarshal(resp.Body, &probe); err != nil {
		return nil, fmt.Errorf("decode /me response: %w", err)
	}
	if probe.Type != "" && probe.Type != "agent" {
		return nil, fmt.Errorf("this token belongs to a %q, not an agent; agent commands require an agent token", probe.Type)
	}
	// A server on an older release reports the grant list as `scopes`. Decoding
	// that into MeAgent would leave Permissions empty — an agent that looks like
	// it holds nothing, which sends it to its operator for grants it already
	// has. Refuse instead, and say why.
	if probe.Permissions == nil && probe.Scopes != nil {
		return nil, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  "the server's GET /me reports `scopes`; this CLI reads `permissions` (the server is on an older release)",
			Actionable: "Keep the CLI on the same release as the server: ask your operator to upgrade the " +
				"server, or install the CLI release that matches it.",
		}
	}
	var agent control.MeAgent
	if err := json.Unmarshal(resp.Body, &agent); err != nil {
		return nil, fmt.Errorf("decode /me response: %w", err)
	}
	return &agent, nil
}
