package api

// admin_cli_profiles.go is `jentic admin cli-profiles provision <profile>`:
// the operator's explicit, idempotent provisioning of a CLI profile's APIs
// (POST /cli-profiles/{profile}:provision). A connect for one of the
// profile's APIs provisions it on demand too; this puts every API in place
// before any agent connects. `jentic cli profiles` lists the profiles.

import (
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

func newAdminCLIProfilesCmd(_ *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "cli-profiles",
		Short: "Provision the APIs of a vendor CLI profile (github, cloudflare)",
		Args:  cobra.NoArgs,
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "provision <profile>",
		Short: "Make every API a CLI profile declares exist and be live (idempotent)",
		Long: "provision imports a CLI profile's APIs so the vendor's CLI finds them all live:\n" +
			"a catalog API is imported from the public catalog (an import job; nothing when it\n" +
			"is already registered), a spec shipped with Jentic (the GitHub GraphQL API) is\n" +
			"imported and made live at once, and a built-in API (GitHub git) needs nothing.\n" +
			"Safe to re-run. Needs catalog:import. Each API's status says what happened\n" +
			"(live, imported, import_queued, builtin, not_in_catalog, failed).",
		Example: "  jentic admin cli-profiles provision github",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			resp, err := client.ProvisionCliProfileWithResponse(cmd.Context(), args[0])
			if err := apiErrorFor(resp, err); err != nil {
				return reportCoded(aud, asCoded(err))
			}
			aud.Render(resp.JSON200)
			return nil
		},
	})
	return cmd
}

func newCLIProfilesCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:   "profiles",
		Short: "List the vendor CLI profiles: their APIs, connect keys and rule presets",
		Long: "profiles answers with the CLI profiles this Jentic ships (GET /cli-profiles): for\n" +
			"each vendor CLI, the APIs it reaches (REST, GraphQL, git) with the `jentic connect`\n" +
			"key of each and whether it is live, and the rule presets `jentic connect --preset`\n" +
			"takes, with their parameters (--preset-param name=value).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			resp, err := client.ListCliProfilesWithResponse(cmd.Context())
			if err := apiErrorFor(resp, err); err != nil {
				return reportCoded(aud, asCoded(err))
			}
			aud.Render(resp.JSON200)
			return nil
		},
	}
}
