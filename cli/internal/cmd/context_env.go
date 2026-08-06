package cmd

import (
	"fmt"
	"sort"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newEnvCmd is the `jentic env` root: manage Control/Broker deployment targets in
// the XDG config (impl/1.3 §3, BC-2). Environments are one leg of the
// Environment × Identity × Context model that replaces flat V1 profiles; the
// migration path from ~/.jentic is `jentic migrate`.
//
// The whole surface is fenced except the read-only `list` — an agent must operate
// within its provisioned environment and never re-point where it talks.
func newEnvCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "env",
		Short: "Manage Control Plane environments (deployment targets)",
		Long: "env manages the deployment targets commands talk to — each an\n" +
			"Environment with a Control Plane base URL and an optional Broker URL.\n" +
			"Environments are referenced by Contexts (`jentic context`). Migrated\n" +
			"from legacy ~/.jentic profiles by `jentic migrate`.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(fenced(newEnvAddCmd(app)))
	cmd.AddCommand(newEnvListCmd(app)) // read-only: not fenced
	cmd.AddCommand(fenced(newEnvDeleteCmd(app)))
	return cmd
}

func newEnvAddCmd(_ *App) *cobra.Command {
	var (
		baseURL   string
		brokerURL string
		caCert    string
		force     bool
	)
	cmd := &cobra.Command{
		Use:   "add <name>",
		Short: "Add a Control Plane environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			aud := ux.FromContext(cmd.Context())

			if err := config.MutateConfig(func(cfg *config.Config) error {
				// NAME GUARD: names become key/token file stems (client/auth
				// Stem, path-traversal guard) — validate at creation.
				if !config.ValidName(name) {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("invalid environment name %q (allowed: ^[a-z0-9][a-z0-9-]{0,63}$)", name),
						Actionable: "Choose a name of lowercase letters, digits and hyphens.",
					}
				}
				// OVERWRITE GUARD: env add on an existing name would silently wipe
				// fields no flag expresses — fail unless --force.
				if _, exists := cfg.Environments[name]; exists && !force {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("environment %q already exists", name),
						Actionable: "Pass --force to replace it, or choose a different name.",
					}
				}
				cfg.Environments[name] = config.Env{
					BaseURL:    baseURL,
					BrokerURL:  brokerURL, // optional; explicit, never derived (BC-4)
					CACertPath: caCert,    // optional; private-CA deployments
				}
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}

			aud.Render(ux.Result{
				Status:   ux.StatusAdded,
				Resource: "environment",
				Name:     name,
				Fields: map[string]any{
					"base_url":   baseURL,
					"broker_url": brokerURL,
				},
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&baseURL, "url", "", "Control Plane base URL")
	cmd.Flags().StringVar(&brokerURL, "broker-url", "", "Data Plane / Broker URL (optional; used by execute and history)")
	cmd.Flags().StringVar(&caCert, "ca-cert", "", "Path to a custom CA bundle for this environment (optional)")
	cmd.Flags().BoolVar(&force, "force", false, "Replace an existing environment of the same name")
	mustMarkRequired(cmd, "url")
	return cmd
}

func newEnvListCmd(_ *App) *cobra.Command {
	return &cobra.Command{
		Use:     "list",
		Aliases: []string{"ls"},
		Short:   "List configured environments",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			cfg, err := config.Load()
			if err != nil {
				return reportCoded(aud, err)
			}
			names := sortedKeys(cfg.Environments)
			items := make([]map[string]any, 0, len(names))
			for _, n := range names {
				e := cfg.Environments[n]
				items = append(items, map[string]any{
					"name":       n,
					"base_url":   e.BaseURL,
					"broker_url": e.BrokerURL,
				})
			}
			aud.Render(ux.NewPage(items, ""))
			return nil
		},
	}
}

func newEnvDeleteCmd(_ *App) *cobra.Command {
	return newResourceDeleteCmd(deleteSpec{
		resource: "environment",
		exists: func(cfg *config.Config, name string) bool {
			_, ok := cfg.Environments[name]
			return ok
		},
		references: contextsReferencingEnv,
		remove: func(cfg *config.Config, name string) {
			delete(cfg.Environments, name)
		},
	})
}

// contextsReferencingEnv returns the names of contexts that point at env.
func contextsReferencingEnv(cfg *config.Config, env string) []string {
	var out []string
	for name, ctx := range cfg.Contexts {
		if ctx.Environment == env {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

// sortedKeys returns a map's keys in deterministic order (list output must be
// stable so golden/agent parsing is reproducible).
func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
