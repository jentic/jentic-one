package api

import (
	"fmt"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newCLICmd is `jentic cli`: the vendor CLIs this Jentic deployment can run
// through its Broker (`list`), and the generic form of `jentic cf` (`run`).
func newCLICmd(a *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "cli",
		Short: "Vendor CLIs (cf, …) you can run through Jentic",
		Long: "Vendor CLIs installed on this machine can run through the Jentic Broker: the\n" +
			"vendor credential stays in Jentic, and every call is checked against your rules\n" +
			"and recorded. `jentic cli list` shows which ones this deployment serves;\n" +
			"`jentic cf …` (or `jentic cli run cf -- …`) runs one.",
	}
	cmd.AddCommand(newCLIListCmd(a), newCLIRunCmd(a))
	return cmd
}

func newCLIListCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List the vendor CLIs this deployment serves, and their facade addresses",
		Long: "list answers with the vendor CLI facades the Broker serves (GET /cli-facades):\n" +
			"for each vendor, the CLI, its addressing mode and the address the wrapper points\n" +
			"the CLI at. A vendor missing here cannot be run through Jentic on this\n" +
			"deployment. Whether you hold a credential for it is checked per call.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			resp, err := client.ListCliFacadesWithResponse(cmd.Context())
			if err := apiErrorFor(resp, err); err != nil {
				return reportCoded(aud, asCoded(err))
			}
			aud.Render(resp.JSON200)
			return nil
		},
	}
}

func newCLIRunCmd(a *app) *cobra.Command {
	names := make([]string, 0, len(vendorProfiles))
	for name := range vendorProfiles {
		names = append(names, name)
	}
	sort.Strings(names)
	return &cobra.Command{
		Use:   "run <cli> [--] [args…]",
		Short: "Run a vendor CLI through Jentic (`jentic cli run cf -- zones list`)",
		Long: "run is the generic form of `jentic <cli>`: `jentic cli run cf -- zones list` is\n" +
			"`jentic cf zones list`. Everything after <cli> (and an optional `--`) is passed\n" +
			"to the vendor CLI untouched. Supported: " + strings.Join(names, ", ") + ".",
		DisableFlagParsing: true,
		Args:               cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 0 || args[0] == "-h" || args[0] == "--help" {
				return cmd.Help()
			}
			prof, ok := vendorProfiles[args[0]]
			if !ok {
				return reportCoded(ux.FromContext(cmd.Context()), &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("unknown vendor CLI %q", args[0]),
					Actionable: "jentic cli run <" + strings.Join(names, "|") + "> -- <args>",
				})
			}
			rest := args[1:]
			if len(rest) > 0 && rest[0] == "--" {
				rest = rest[1:]
			}
			return a.runVendorCLI(cmd, prof, rest)
		},
	}
}
