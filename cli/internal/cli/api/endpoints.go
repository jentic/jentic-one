package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// endpointsOptions holds flags for `jentic endpoints`.
type endpointsOptions struct {
	json  bool
	scope string
	actor string
}

func newEndpointsCmd(app *app) *cobra.Command {
	o := &endpointsOptions{}
	cmd := &cobra.Command{
		Use:   "endpoints",
		Short: "Browse the API endpoint + scope reference",
		Long: "endpoints prints every control-plane API endpoint grouped by its typical\n" +
			"caller and the scope(s) it requires. The grouping is an advisory hint — the\n" +
			"scope is the real gate. It reads the server's public endpoint reference\n" +
			"(/reference/endpoints.json, no token needed) — the same join published in\n" +
			"docs/reference/endpoints.md.\n" +
			"Filter with --scope or --actor, or use --json for a machine-readable dump.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.endpointsE(cmd.Context(), o)
		},
	}
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	cmd.Flags().StringVar(&o.scope, "scope", "", "only endpoints requiring this scope")
	cmd.Flags().StringVar(&o.actor, "actor", "", "only endpoints callable by this actor type (user, agent, service_account, toolkit)")
	return cmd
}

// endpoint is one (method, path) row with its recovered auth metadata.
type endpoint struct {
	Method        string   `json:"method"`
	Path          string   `json:"path"`
	Summary       string   `json:"summary"`
	Public        bool     `json:"public"`
	ActorTypes    []string `json:"actor_types"`
	Scopes        []string `json:"required_scopes"`
	AuthNote      string   `json:"auth_note,omitempty"`
	TypicalCaller string   `json:"typical_caller,omitempty"`
}

func (a *app) endpointsE(ctx context.Context, o *endpointsOptions) error {
	// endpoints is unauthenticated (reads the public reference endpoint), so only
	// the base URL matters — the active context's environment URL.
	st, err := a.requireState(ctx)
	if err != nil {
		return err
	}
	if st.BaseURL == "" {
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("environment %q has no base_url", st.EnvironmentName),
			Actionable: "Set it with `jentic env add` / edit the environment.",
		}
	}
	baseURL := st.BaseURL
	// endpoints reads the public /reference route (no token needed). Use a
	// raw-only apiClient so we don't force the credential pre-flight that the
	// authenticated apis commands do (an unregistered-but-configured context can
	// still browse the reference).
	client := &apiClient{}
	body, err := client.Reference(ctx)
	if err != nil {
		return endpointsFetchErr(err, baseURL)
	}

	eps, err := parseEndpoints(body)
	if err != nil {
		return err
	}
	eps = filterEndpoints(eps, o.scope, o.actor)

	if o.json {
		if eps == nil {
			eps = []endpoint{}
		}
		return cmdcore.WriteList(a.Out, eps, "", nil)
	}
	a.printEndpoints(eps)
	return nil
}

// parseEndpoints reads the endpoint reference payload served at
// /reference/endpoints.json (schema jentic.endpoint-scope-tree/v1). The server
// builds it from its curated scope map, so the CLI consumes the join directly
// rather than re-deriving authorization from the OpenAPI document.
func parseEndpoints(body []byte) ([]endpoint, error) {
	var doc struct {
		Endpoints []endpoint `json:"endpoints"`
	}
	if err := json.Unmarshal(body, &doc); err != nil {
		return nil, fmt.Errorf("parse endpoint reference: %w", err)
	}
	eps := doc.Endpoints
	sort.Slice(eps, func(i, j int) bool {
		if eps[i].Path != eps[j].Path {
			return eps[i].Path < eps[j].Path
		}
		return eps[i].Method < eps[j].Method
	})
	return eps, nil
}

func filterEndpoints(eps []endpoint, scope, actor string) []endpoint {
	if scope == "" && actor == "" {
		return eps
	}
	var out []endpoint
	for _, ep := range eps {
		if scope != "" && !contains(ep.Scopes, scope) {
			continue
		}
		if actor != "" && !contains(ep.ActorTypes, actor) {
			continue
		}
		out = append(out, ep)
	}
	return out
}

// group classifies an endpoint by its typical caller, mirroring
// tools/endpoint_tree.py. The grouping is an advisory hint (the scope is the real
// gate). The fields come from the /reference/endpoints.json payload — the
// server reports typical_caller (agent/operator/any) and a public flag, so the
// Public flag is the authority for public, not any OpenAPI vendor extension.
func (ep endpoint) group() string {
	if ep.Public {
		return groupPublic
	}
	switch ep.TypicalCaller {
	case "agent":
		return groupAgent
	case "operator":
		return groupOperator
	default:
		return groupAny
	}
}

const (
	groupAgent    = "Agent-facing (typically agent / service-account / toolkit)"
	groupOperator = "Operator-facing (typically a human operator / admin)"
	groupAny      = "Any authenticated actor"
	groupPublic   = "Public (unauthenticated)"
)

var groupOrder = []string{
	groupAgent,
	groupOperator,
	groupAny,
	groupPublic,
}

func (a *app) printEndpoints(eps []endpoint) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Endpoint & scope reference"))
	if len(eps) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no endpoints match the filter"))
		return
	}
	grouped := map[string][]endpoint{}
	for _, ep := range eps {
		g := ep.group()
		grouped[g] = append(grouped[g], ep)
	}
	for _, g := range groupOrder {
		rows := grouped[g]
		if len(rows) == 0 {
			continue
		}
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Accent.Render(fmt.Sprintf("%s (%d)", g, len(rows))))
		for _, ep := range rows {
			fmt.Fprintln(a.Out, "  "+endpointLine(ep))
		}
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("%d endpoint(s)", len(eps))))
}

func endpointLine(ep endpoint) string {
	line := theme.Command.Render(fmt.Sprintf("%-6s", ep.Method)) + " " + ep.Path
	scopes := "public — no auth"
	if !ep.Public {
		if len(ep.Scopes) > 0 {
			scopes = strings.Join(ep.Scopes, ", ")
		} else {
			scopes = "any authenticated"
		}
	}
	line += "  " + theme.Dim.Render("→ "+scopes)
	if !ep.Public && ep.TypicalCaller != "" && ep.TypicalCaller != "any" {
		line += " " + theme.Dim.Render("[typically: "+ep.TypicalCaller+"]")
	}
	if ep.AuthNote != "" {
		line += " " + theme.Dim.Render("("+ep.AuthNote+")")
	}
	return line
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}

// endpointsFetchErr maps a transport/HTTP failure to a friendly message.
func endpointsFetchErr(err error, baseURL string) error {
	var he *HTTPError
	if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
		return fmt.Errorf("server at %s does not expose /reference/endpoints.json", baseURL)
	}
	return fmt.Errorf("could not reach control plane at %s: %w", baseURL, err)
}
