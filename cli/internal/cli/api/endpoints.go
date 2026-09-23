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

// endpointReferenceSchema is the payload schema id of /reference/endpoints.json
// this CLI understands (tools/endpoint_tree.py and endpoint_reference.py emit it).
const endpointReferenceSchema = "jentic.endpoint-permission-tree/v1"

// endpointsOptions holds flags for `jentic endpoints`.
type endpointsOptions struct {
	json       bool
	permission string
	actor      string
}

func newEndpointsCmd(app *app) *cobra.Command {
	o := &endpointsOptions{}
	cmd := &cobra.Command{
		Use:   "endpoints",
		Short: "Browse the API endpoint + permission reference",
		Long: "endpoints prints every control-plane API endpoint grouped by its typical\n" +
			"caller and the permission(s) it requires. The grouping is an advisory hint —\n" +
			"the permission is the real gate. It reads the server's public endpoint\n" +
			"reference (/reference/endpoints.json, no token needed) — the same join\n" +
			"published in docs/reference/endpoints.md.\n" +
			"Filter with --permission or --actor, or use --json for a machine-readable dump.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.endpointsE(cmd.Context(), o)
		},
	}
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	cmd.Flags().StringVar(&o.permission, "permission", "", "only endpoints requiring this permission")
	cmd.Flags().StringVar(&o.actor, "actor", "", "only endpoints callable by this actor type (user, agent)")
	return cmd
}

// endpoint is one (method, path) row with its recovered auth metadata.
type endpoint struct {
	Method        string   `json:"method"`
	Path          string   `json:"path"`
	Summary       string   `json:"summary"`
	Public        bool     `json:"public"`
	ActorTypes    []string `json:"actor_types"`
	Permissions   []string `json:"required_permissions"`
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
	eps = filterEndpoints(eps, o.permission, o.actor)

	if o.json {
		if eps == nil {
			eps = []endpoint{}
		}
		return cmdcore.WriteList(a.Out, eps, "", nil)
	}
	a.printEndpoints(ctx, eps)
	return nil
}

// parseEndpoints reads the endpoint reference payload served at
// /reference/endpoints.json (schema jentic.endpoint-permission-tree/v1). The
// server builds it from its curated permission map, so the CLI consumes the join directly
// rather than re-deriving authorization from the OpenAPI document.
//
// The payload's `schema` id is checked, not just decoded: its field names are
// part of the contract (`required_permissions`), so a server on another release
// would otherwise decode into endpoints that silently require nothing.
func parseEndpoints(body []byte) ([]endpoint, error) {
	var doc struct {
		Schema    string     `json:"schema"`
		Endpoints []endpoint `json:"endpoints"`
	}
	if err := json.Unmarshal(body, &doc); err != nil {
		return nil, fmt.Errorf("parse endpoint reference: %w", err)
	}
	if doc.Schema != endpointReferenceSchema {
		return nil, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg: fmt.Sprintf("the server's endpoint reference uses schema %q; this CLI reads %q",
				doc.Schema, endpointReferenceSchema),
			Actionable: "Keep the CLI on the same release as the server: run `jenticctl update`, " +
				"or ask your operator which release the server runs.",
		}
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

func filterEndpoints(eps []endpoint, permission, actor string) []endpoint {
	if permission == "" && actor == "" {
		return eps
	}
	var out []endpoint
	for _, ep := range eps {
		if permission != "" && !contains(ep.Permissions, permission) {
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
// tools/endpoint_tree.py. The grouping is an advisory hint (the permission is the
// real gate). The fields come from the /reference/endpoints.json payload — the
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
	groupAgent    = "Agent-facing (typically an agent)"
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

func (a *app) printEndpoints(ctx context.Context, eps []endpoint) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("Endpoint & permission reference"))
	if len(eps) == 0 {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("no endpoints match the filter"))
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
		fmt.Fprintln(a.Out, st.Accent.Render(fmt.Sprintf("%s (%d)", g, len(rows))))
		for _, ep := range rows {
			fmt.Fprintln(a.Out, "  "+endpointLine(st, ep))
		}
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, st.Dim.Render(fmt.Sprintf("%d endpoint(s)", len(eps))))
}

func endpointLine(st theme.Styles, ep endpoint) string {
	line := st.Command.Render(fmt.Sprintf("%-6s", ep.Method)) + " " + ep.Path
	permissions := "public — no auth"
	if !ep.Public {
		if len(ep.Permissions) > 0 {
			permissions = strings.Join(ep.Permissions, ", ")
		} else {
			permissions = "any authenticated"
		}
	}
	line += "  " + st.Dim.Render("→ "+permissions)
	if !ep.Public && ep.TypicalCaller != "" && ep.TypicalCaller != "any" {
		line += " " + st.Dim.Render("[typically: "+ep.TypicalCaller+"]")
	}
	if ep.AuthNote != "" {
		line += " " + st.Dim.Render("("+ep.AuthNote+")")
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
