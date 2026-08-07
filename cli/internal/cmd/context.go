package cmd

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newContextCmd is the `jentic context` root: bind an environment + identity +
// mode into a named context and switch between them (impl/1.3 §4, kubectl-style).
// Contexts replace flat V1 profiles as the selection unit.
//
// Fenced surface: create/use/delete mutate or re-point which environment/identity
// is active — forbidden for agents. `list` and `view` are read-only carve-outs
// (view shows only the ACTIVE context so an agent can introspect itself).
func newContextCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "context",
		Short: "Manage and switch contexts (environment + identity + mode)",
		Long: "context binds an Environment, an Identity, and an interaction mode into a\n" +
			"named context, and switches which one commands act on. It is the V2\n" +
			"successor to `jentic profile` (see `jentic migrate`).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(fenced(newContextCreateCmd(app)))
	cmd.AddCommand(fenced(newContextUseCmd(app)))
	cmd.AddCommand(newContextListCmd(app)) // read-only: not fenced
	cmd.AddCommand(newContextViewCmd(app)) // read-only (active only): not fenced
	cmd.AddCommand(fenced(newContextDeleteCmd(app)))
	return cmd
}

func newContextCreateCmd(_ *App) *cobra.Command {
	var (
		env   string
		ident string
		mode  string
		use   bool
		force bool
	)
	cmd := &cobra.Command{
		Use:   "create <name>",
		Short: "Create a context binding an environment + identity + mode",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			aud := ux.FromContext(cmd.Context())

			if mode == "" {
				mode = clictx.ModeHuman
			}
			if err := config.MutateConfig(func(cfg *config.Config) error {
				if !config.ValidName(name) {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("invalid context name %q (allowed: ^[a-z0-9][a-z0-9-]{0,63}$)", name),
						Actionable: "Choose a name of lowercase letters, digits and hyphens.",
					}
				}
				if _, exists := cfg.Contexts[name]; exists && !force {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("context %q already exists", name),
						Actionable: "Pass --force to replace it, or choose a different name.",
					}
				}
				// Referential integrity: the context must point at things that
				// exist — a dangling context is a runtime error waiting to happen.
				if _, ok := cfg.Environments[env]; !ok {
					return &ux.CodedError{
						Code:       ux.CodeResolveFailed,
						Msg:        fmt.Sprintf("environment %q does not exist", env),
						Actionable: "Create it first with `jentic env add`.",
					}
				}
				if _, ok := cfg.Identities[ident]; !ok {
					return &ux.CodedError{
						Code:       ux.CodeResolveFailed,
						Msg:        fmt.Sprintf("identity %q does not exist", ident),
						Actionable: "Create it first with `jentic identity add`.",
					}
				}
				cfg.Contexts[name] = config.Context{Environment: env, Identity: ident, Mode: mode}
				if use {
					cfg.ActiveContext = name
				}
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}

			status := ux.StatusCreated
			if use {
				status = ux.StatusSwitched
			}
			aud.Render(ux.Result{
				Status:   status,
				Resource: "context",
				Name:     name,
				Fields: map[string]any{
					"environment": env,
					"identity":    ident,
					"mode":        mode,
				},
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&env, "env", "", "Environment this context targets")
	cmd.Flags().StringVar(&ident, "identity", "", "Identity this context acts as")
	cmd.Flags().StringVar(&mode, "mode", "", "Interaction mode: human|agent|service-account (default human)")
	cmd.Flags().BoolVar(&use, "use", false, "Set this as the active context after creating it")
	cmd.Flags().BoolVar(&force, "force", false, "Replace an existing context of the same name")
	mustMarkRequired(cmd, "env")
	mustMarkRequired(cmd, "identity")
	return cmd
}

func newContextUseCmd(_ *App) *cobra.Command {
	return &cobra.Command{
		Use:   "use <name>",
		Short: "Set the active context",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			aud := ux.FromContext(cmd.Context())
			if err := config.MutateConfig(func(cfg *config.Config) error {
				if _, ok := cfg.Contexts[name]; !ok {
					return &ux.CodedError{
						Code:       ux.CodeResolveFailed,
						Msg:        fmt.Sprintf("context %q does not exist", name),
						Actionable: "Run `jentic context list` to see options.",
					}
				}
				cfg.ActiveContext = name
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}
			aud.Render(ux.Result{Status: ux.StatusSwitched, Resource: "context", Name: name})
			return nil
		},
	}
}

func newContextListCmd(_ *App) *cobra.Command {
	return &cobra.Command{
		Use:     "list",
		Aliases: []string{"ls"},
		Short:   "List contexts and mark the active one",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			cfg, err := config.Load()
			if err != nil {
				return reportCoded(aud, err)
			}
			names := sortedKeys(cfg.Contexts)
			items := make([]map[string]any, 0, len(names))
			for _, n := range names {
				c := cfg.Contexts[n]
				items = append(items, map[string]any{
					"name":        n,
					"environment": c.Environment,
					"identity":    c.Identity,
					"mode":        c.Mode,
					"active":      n == cfg.ActiveContext,
				})
			}
			aud.Render(ux.NewPage(items, ""))
			return nil
		},
	}
}

// newContextViewCmd shows ONLY the active context (impl/1.3 §3 carve-out): an
// agent can introspect what it is bound to without the ability to enumerate or
// switch to other contexts. Deliberately read-only and unfenced.
func newContextViewCmd(_ *App) *cobra.Command {
	return &cobra.Command{
		Use:   "view",
		Short: "Show the active context",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			cfg, err := config.Load()
			if err != nil {
				return reportCoded(aud, err)
			}
			active := cfg.ActiveContext
			c, ok := cfg.Contexts[active]
			if active == "" || !ok {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeResolveFailed,
					Msg:        "no active context",
					Actionable: "Create one with `jentic context create` or run `jentic migrate`.",
				})
			}
			aud.Render(ux.Result{
				Status:   "active",
				Resource: "context",
				Name:     active,
				Fields: map[string]any{
					"environment": c.Environment,
					"identity":    c.Identity,
					"mode":        c.Mode,
				},
			})
			return nil
		},
	}
}

func newContextDeleteCmd(_ *App) *cobra.Command {
	var (
		yes          bool
		withIdentity bool
	)
	cmd := &cobra.Command{
		Use:   "delete <name>",
		Short: "Delete a context (the successor of `profile delete`)",
		Long: "delete removes a context. It refuses to delete the ACTIVE context\n" +
			"(switch first) to avoid a dangling active_context. With --identity it also\n" +
			"removes the referenced identity iff no other context uses it.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			aud := ux.FromContext(cmd.Context())

			cfg, err := config.Load()
			if err != nil {
				return reportCoded(aud, err)
			}
			target, ok := cfg.Contexts[name]
			if !ok {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg:  fmt.Sprintf("context %q does not exist", name),
				})
			}
			if cfg.ActiveContext == name {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("cannot delete the active context %q", name),
					Actionable: "Switch to another context first with `jentic context use <other>`.",
				})
			}

			ok2, cerr := aud.AskConfirm(fmt.Sprintf("Delete context %q?", name))
			if cerr != nil {
				return reportCoded(aud, cerr)
			}
			if !ok2 {
				return nil
			}

			if err := config.MutateConfig(func(cfg *config.Config) error {
				delete(cfg.Contexts, name)
				// --identity: GC the referenced identity iff no other context
				// still uses it (never implicit — identities/envs are not
				// garbage-collected without the flag, impl/1.3 §4a).
				if withIdentity && target.Identity != "" && !identityStillReferenced(cfg, target.Identity) {
					delete(cfg.Identities, target.Identity)
				}
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}
			aud.Render(ux.Result{Status: ux.StatusDeleted, Resource: "context", Name: name})
			return nil
		},
	}
	cmd.Flags().BoolVarP(&yes, "yes", "y", false, "Skip the confirmation prompt")
	cmd.Flags().BoolVar(&withIdentity, "identity", false, "Also delete the referenced identity if unused elsewhere")
	return cmd
}

// identityStillReferenced reports whether any context binds identity ident.
func identityStillReferenced(cfg *config.Config, ident string) bool {
	for _, c := range cfg.Contexts {
		if c.Identity == ident {
			return true
		}
	}
	return false
}
