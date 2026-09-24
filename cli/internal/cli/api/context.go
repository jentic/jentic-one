package api

import (
	"fmt"
	"log/slog"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
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
func newContextCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "context",
		Short: "Manage and switch contexts (environment + identity + mode)",
		Long: "context binds an Environment, an Identity, and an interaction mode into a\n" +
			"named context, and switches which one commands act on. It is the\n" +
			"successor to the old `jentic profile` (see `jentic migrate`).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			// The most common question a user asks here is "which context am I
			// in?" (UX5). On the bare invocation, answer that (active context +
			// a one-line pointer to the subcommands) and stop — burying the
			// 4-line state under the full logo/USAGE/COMMANDS help dump made it
			// invisible. Reserve the full help for an explicit -h/--help, which
			// short-circuits RunE before we get here.
			if showActiveContext(cmd, true) {
				return nil
			}
			// Nothing active (fresh machine / unreadable config): fall through to
			// the subcommand list rather than a bare "no context" line.
			return cmd.Help()
		},
	}
	cmd.AddCommand(fenced(newContextCreateCmd(app)))
	cmd.AddCommand(fenced(newContextUseCmd(app)))
	cmd.AddCommand(fenced(newContextListCmd(app))) // fenced: enumerates the operator's OTHER identities/contexts (impl/3.2 §2a)
	cmd.AddCommand(newContextViewCmd(app))         // read-only (active only): not fenced
	cmd.AddCommand(fenced(newContextRenameCmd(app)))
	cmd.AddCommand(fenced(newContextDeleteCmd(app)))
	return cmd
}

// showActiveContext prints the active context (the same Result `context view`
// renders) as an ambient "you are here". It reports whether it rendered
// anything: false when nothing is active (or the config can't be read), so a
// fresh machine drops straight to help instead of surfacing a scary "no active
// context" error where the user only asked for the command list. When withHint
// is true the rendered Result carries a one-line pointer to the subcommands in
// its Message field (a typed envelope field, so agent-mode JSON stays clean —
// no naked stdout write).
func showActiveContext(cmd *cobra.Command, withHint bool) bool {
	cfg, err := config.Load()
	if err != nil {
		return false
	}
	active := cfg.ActiveContext
	c, ok := cfg.Contexts[active]
	if active == "" || !ok {
		return false
	}
	var msg string
	if withHint {
		msg = "· run `jentic context --help` for subcommands (create, use, list, view, rename, delete)"
	}
	ux.FromContext(cmd.Context()).Render(ux.Result{
		Status:   "active",
		Resource: "context",
		Name:     active,
		Message:  msg,
		Fields: map[string]any{
			"environment": c.Environment,
			"identity":    c.Identity,
			"mode":        c.Mode,
		},
	})
	return true
}

func newContextCreateCmd(_ *app) *cobra.Command {
	var (
		env   string
		ident string
		mode  string
		use   bool
		force bool
	)
	cmd := &cobra.Command{
		Use: "create <name>",
		// "add" aligns the verb with `env add`/`identity add` (UX-6); "create"
		// stays the canonical name.
		Aliases: []string{"add"},
		Short:   "Create a context binding an environment + identity + mode",
		Long: "Create a context — a named binding of an environment (--env), an identity\n" +
			"(--identity), and a mode (--mode, default human). Contexts are what commands\n" +
			"act through; --use activates the new one immediately. Most people never run\n" +
			"this directly — `jentic register --url …` creates the trio for you.",
		Example: "  jentic context create prod --env prod --identity crawler --use\n" +
			"  jentic context create local --env local --identity laptop --mode agent",
		Args: exactNamedArgs("<name>", "name"),
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

func newContextUseCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:   "use <name>",
		Short: "Set the active context",
		Long: "Set the active context — the environment + identity + mode that every\n" +
			"command acts through when you don't pass --context. The named context must\n" +
			"already exist (`jentic context list`); this only switches which one is active.",
		Example: "  jentic context use prod\n" +
			"  jentic context use local   # then: jentic catalog / jentic execute …",
		Args: exactNamedArgs("<name>", "name"),
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

func newContextListCmd(_ *app) *cobra.Command {
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
func newContextViewCmd(_ *app) *cobra.Command {
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

// newContextRenameCmd renames a context (fenced: it mutates host config, same
// class as create/use/delete). It moves the map key and, if the renamed context
// is active, repoints active_context so the rename never orphans the selection.
func newContextRenameCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:   "rename <old> <new>",
		Short: "Rename a context",
		Long: "rename changes a context's name (e.g. an auto-derived `qa1-crawler` to\n" +
			"something friendlier) without delete+recreate. If the renamed context is\n" +
			"active, the active selection follows the new name.",
		Example: "  jentic context rename qa1-crawler qa",
		Args:    cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			oldName, newName := args[0], args[1]
			aud := ux.FromContext(cmd.Context())
			if err := config.MutateConfig(func(cfg *config.Config) error {
				c, ok := cfg.Contexts[oldName]
				if !ok {
					return &ux.CodedError{
						Code:       ux.CodeResolveFailed,
						Msg:        fmt.Sprintf("context %q does not exist", oldName),
						Actionable: "Run `jentic context list` to see options.",
					}
				}
				if oldName == newName {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("context is already named %q", newName),
						Actionable: "Choose a different new name.",
					}
				}
				if !config.ValidName(newName) {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("invalid context name %q (allowed: ^[a-z0-9][a-z0-9-]{0,63}$)", newName),
						Actionable: "Choose a name of lowercase letters, digits and hyphens.",
					}
				}
				if _, exists := cfg.Contexts[newName]; exists {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("context %q already exists", newName),
						Actionable: "Choose a different new name, or delete the existing one first.",
					}
				}
				cfg.Contexts[newName] = c
				delete(cfg.Contexts, oldName)
				if cfg.ActiveContext == oldName {
					cfg.ActiveContext = newName
				}
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}
			aud.Render(ux.Result{Status: "renamed", Resource: "context", Name: newName})
			return nil
		},
	}
}

func newContextDeleteCmd(_ *app) *cobra.Command {
	var withIdentity bool
	cmd := &cobra.Command{
		Use:     "delete <name>",
		Aliases: []string{"rm"},
		Short:   "Delete a context",
		Long: "delete removes a context (the equivalent of removing a legacy profile).\n" +
			"It refuses to delete the ACTIVE context\n" +
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
			// Fast-fail preflight on the unlocked snapshot for good UX (don't prompt
			// to delete something that doesn't exist / is active). This is advisory;
			// the AUTHORITATIVE check runs inside the lock below (F1).
			if _, ok := cfg.Contexts[name]; !ok {
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

			// Capture the identity that MIGHT be GC'd (with --identity) so we can
			// purge its orphaned secret files after a successful mutate. Whether it
			// is actually removed is decided INSIDE the lock, after the context is
			// deleted, against the mutator's own cfg — see below.
			var purgeIdentity string
			var toPurge []auth.IdentityRef

			if err := config.MutateConfig(func(cfg *config.Config) error {
				// Re-validate INSIDE the lock (F1, review round-3 #4): existence and
				// active-context status were checked on an unlocked snapshot before a
				// confirmation prompt that can block indefinitely. Re-check against
				// the mutator's fresh cfg so a concurrent `context use`/`context
				// rename`/delete can't make us delete the (now-)active context or a
				// context that already vanished.
				target, ok := cfg.Contexts[name]
				if !ok {
					return &ux.CodedError{
						Code: ux.CodeResolveFailed,
						Msg:  fmt.Sprintf("context %q does not exist", name),
					}
				}
				if cfg.ActiveContext == name {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("cannot delete the active context %q", name),
						Actionable: "Switch to another context first with `jentic context use <other>`.",
					}
				}
				delete(cfg.Contexts, name)
				// --identity: GC the referenced identity iff NO OTHER context still
				// uses it (never implicit — identities/envs are not garbage-collected
				// without the flag, impl/1.3 §4a). The check runs AFTER the delete so
				// identityStillReferenced reflects the remaining contexts, and against
				// the mutator's cfg (not a stale outer snapshot) so a concurrently
				// created context that binds the identity keeps it alive.
				if withIdentity && target.Identity != "" && !identityStillReferenced(cfg, target.Identity) {
					purgeIdentity = target.Identity
					toPurge = identityMaterialRefs(cfg, target.Identity)
					delete(cfg.Identities, target.Identity)
				}
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}

			// Purge the GC'd identity's orphaned secret files (F8-34). Non-fatal:
			// the context/identity are already removed from config.
			if purgeIdentity != "" {
				for _, ref := range toPurge {
					if perr := auth.PurgeMaterial(ref); perr != nil {
						slog.Warn("could not remove orphaned identity secret files",
							"identity", ref.Identity, "environment", ref.Environment, "error", perr)
					}
				}
			}

			aud.Render(ux.Result{Status: ux.StatusDeleted, Resource: "context", Name: name})
			return nil
		},
	}
	// --yes is consumed by the root interceptor (it wires the Audience's
	// assumeYes); this command only needs the flag to EXIST for that lookup, so
	// bind it without a dead local (F8-23).
	cmd.Flags().BoolP("yes", "y", false, "Skip the confirmation prompt")
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
