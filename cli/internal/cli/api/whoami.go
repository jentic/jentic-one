package api

import (
	"encoding/json"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newWhoamiCmd is `jentic whoami`: the identity self-check — a nicer form of
// `jentic api GET /me`. It renders the discriminated union GET /me returns
// (user | agent | service-account) verbatim: unlike getMe (me.go), which
// deliberately rejects non-agent discriminators for the agent-only data
// commands, whoami is exactly the "who am I?" question and must answer it for
// every token kind. Not fenced — read-only, no local config mutation
// (mirrors `jentic credentials`).
func newWhoamiCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:   "whoami",
		Short: "Show who you are: identity, status, scopes, and credential bindings",
		Long: "whoami answers with your identity as the control plane sees it — the\n" +
			"authenticated GET /me response, verbatim. For an agent that is the id,\n" +
			"status, scopes, and credential bindings with the APIs each one serves;\n" +
			"user and service-account tokens render their own /me variant. Check it\n" +
			"before executing anything new — access is decided from your bindings,\n" +
			"never by probing with execute.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			resp, err := client.GetMeWithResponse(cmd.Context())
			if err := apiErrorFor(resp, err); err != nil {
				return reportCoded(aud, asCoded(err))
			}
			// Decode the raw body rather than a typed variant: /me is a
			// discriminated union and every variant must render as-is (the
			// generated AsMe* accessors do not validate the discriminator,
			// and a typed projection would silently drop the other
			// variants' fields).
			var me map[string]any
			if err := json.Unmarshal(resp.Body, &me); err != nil {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeInternalError,
					Msg:  "decode /me response: " + err.Error(),
				})
			}
			aud.Render(me)
			return nil
		},
	}
}
