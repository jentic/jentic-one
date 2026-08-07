package api

import (
	"crypto/ed25519"
	"fmt"
	"sort"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newIdentityCmd is the `jentic identity` root: manage actors (agents/users) in
// the XDG config (impl/1.3, BC-2/BC-3). An Identity is one leg of the
// Environment × Identity × Context model; per-environment registration state
// (client_id/status) is written by `jentic identity register` (Phase 4).
//
// `add`/`delete` mutate management state and are fenced; `register` is the
// deliberate carve-out (an agent legitimately registers its OWN identity in its
// provisioned environment — impl/1.3 §5) and `list` is read-only.
func newIdentityCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "identity",
		Short: "Manage identities (agents and users) and their credentials",
		Long: "identity manages the actors commands act as. Bind an identity to an\n" +
			"environment through a Context (`jentic context`). API-key identities carry\n" +
			"a jak_* credential; agent identities register via `jentic register`.\n" +
			"Migrated from legacy ~/.jentic profiles by `jentic migrate`.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(fenced(newIdentityAddCmd(app)))
	cmd.AddCommand(newIdentityListCmd(app)) // read-only: not fenced
	cmd.AddCommand(newIdentityRegisterCmd(app))
	cmd.AddCommand(fenced(newIdentityDeleteCmd(app)))
	return cmd
}

// newIdentityRegisterCmd implements `jentic identity register` (Phase 4.1 §2):
// the active identity registers its environment-scoped Ed25519 public key with the
// active environment via RFC 7591 Dynamic Client Registration, and the returned
// client_id + status ("pending" until an operator approves) are persisted per
// (identity, environment) in config.yaml. A subsequent token exchange (the auth
// middleware) signs assertions with that exact key, scoped to that environment.
//
// This is the deliberate FENCING CARVE-OUT: registration is the one management-
// shaped step an agent legitimately performs for its OWN provisioned identity
// during autonomous bootstrap, so it is bootstrap-safe rather than fenced
// (07_security_and_agent_isolation; impl/1.3 §5). It never switches contexts or
// mints another identity.
func newIdentityRegisterCmd(_ *app) *cobra.Command {
	var name string
	cmd := bootstrapSafe(&cobra.Command{
		Use:   "register",
		Short: "Register the active identity's key with the active environment",
		Long: "register generates an environment-scoped Ed25519 key (if absent),\n" +
			"performs RFC 7591 Dynamic Client Registration, and records the returned\n" +
			"client_id + status. The agent is 'pending' until an operator approves it;\n" +
			"token exchange then succeeds automatically. This is the V2 successor to the\n" +
			"legacy `jentic register` and operates on the resolved active context.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			state := clictx.FromContext(cmd.Context())
			if state == nil || state.ResolvedState == nil || state.IdentityName == "" || state.EnvironmentName == "" {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeResolveFailed,
					Msg:        "no active identity/environment to register",
					Actionable: "Create and select a context first (`jentic context create`, `jentic context use`).",
				})
			}
			if state.BaseURL == "" {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeResolveFailed,
					Msg:        fmt.Sprintf("environment %q has no base_url", state.EnvironmentName),
					Actionable: "Set it with `jentic env add` / edit the environment.",
				})
			}

			ref := auth.IdentityRef{Identity: state.IdentityName, Environment: state.EnvironmentName}

			// Local-only, non-destructive: mint the env-scoped key if this is the
			// first registration for the pair. Never contacts the server.
			priv, err := auth.GetOrGenerateKey(ref)
			if err != nil {
				return reportCoded(aud, err)
			}
			pub, ok := priv.Public().(ed25519.PublicKey)
			if !ok {
				return reportCoded(aud, &ux.CodedError{Code: ux.CodeInternalError, Msg: "env-scoped key is not Ed25519"})
			}

			clientName := name
			if clientName == "" {
				clientName = state.IdentityName
			}
			reg, err := auth.Register(state.BaseURL, clientName, auth.PublicKeyToJWKS(pub))
			if err != nil {
				return reportCoded(aud, err)
			}

			if err := config.MutateConfig(func(cfg *config.Config) error {
				ident := cfg.Identities[state.IdentityName]
				if ident.Environments == nil {
					ident.Environments = make(map[string]config.EnvRegState)
				}
				status := reg.Status
				if status == "" {
					status = "pending"
				}
				ident.Environments[state.EnvironmentName] = config.EnvRegState{
					ClientID: reg.ClientID,
					Status:   status,
				}
				cfg.Identities[state.IdentityName] = ident
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}

			status := reg.Status
			if status == "" {
				status = "pending"
			}
			renderStatus := ux.StatusPending
			msg := "Agent registered. Operator approval required before use."
			if status == "approved" {
				renderStatus = ux.StatusRegistered
				msg = "Agent registered and approved."
			}
			aud.Render(ux.Result{
				Status:   renderStatus,
				Resource: "identity",
				Name:     state.IdentityName,
				ID:       reg.ClientID,
				Message:  msg,
				Fields: map[string]any{
					"environment": state.EnvironmentName,
					"status":      status,
				},
			})
			return nil
		},
	})
	cmd.Flags().StringVar(&name, "name", "", "Client name shown to the approving operator (default: identity name)")
	return cmd
}

func newIdentityAddCmd(_ *app) *cobra.Command {
	var (
		idType string
		apiKey string
		env    string
		force  bool
	)
	cmd := &cobra.Command{
		Use:   "add <name>",
		Short: "Add an identity (optionally with a jak_* API key credential)",
		Long: "add records an identity in the config. With --api-key it stores a jak_*\n" +
			"credential for the given --env (the V2 successor to `jentic profile\n" +
			"add-key`). Agent identities obtain tokens later via `jentic register`.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			aud := ux.FromContext(cmd.Context())

			if idType == "" {
				idType = "agent"
			}
			if idType != "agent" && idType != "user" {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("invalid identity type %q", idType),
					Actionable: "Use --type agent or --type user.",
				})
			}
			// --api-key requires --env: the credential is stored per
			// (identity, environment) under its file stem.
			if apiKey != "" && env == "" {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        "--api-key requires --env (credentials are scoped per identity+environment)",
					Actionable: "Pass --env <name> alongside --api-key.",
				})
			}

			if err := config.MutateConfig(func(cfg *config.Config) error {
				if !config.ValidName(name) {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("invalid identity name %q (allowed: ^[a-z0-9][a-z0-9-]{0,63}$)", name),
						Actionable: "Choose a name of lowercase letters, digits and hyphens.",
					}
				}
				if _, exists := cfg.Identities[name]; exists && !force {
					return &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        fmt.Sprintf("identity %q already exists", name),
						Actionable: "Pass --force to replace it, or choose a different name.",
					}
				}
				if apiKey != "" {
					if _, ok := cfg.Environments[env]; !ok {
						return &ux.CodedError{
							Code:       ux.CodeResolveFailed,
							Msg:        fmt.Sprintf("environment %q does not exist", env),
							Actionable: "Create it first with `jentic env add`.",
						}
					}
				}
				cfg.Identities[name] = config.Identity{Type: idType}
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}

			// Store the secret credential OUTSIDE config.yaml (XDG state, 0600).
			if apiKey != "" {
				ref := auth.IdentityRef{Identity: name, Environment: env}
				if err := auth.SaveAPIKey(ref, apiKey); err != nil {
					return reportCoded(aud, err)
				}
			}

			fields := map[string]any{"type": idType}
			if apiKey != "" {
				fields["environment"] = env
				// The key value itself is redacted by the funnel via the key
				// heuristic; carry only that a credential was stored.
				fields["api_key_stored"] = true
			}
			aud.Render(ux.Result{
				Status:   ux.StatusAdded,
				Resource: "identity",
				Name:     name,
				Fields:   fields,
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&idType, "type", "", "Identity type: agent|user (default agent)")
	cmd.Flags().StringVar(&apiKey, "api-key", "", "A jak_* API key credential to store for --env")
	cmd.Flags().StringVar(&env, "env", "", "Environment the --api-key credential is scoped to")
	cmd.Flags().BoolVar(&force, "force", false, "Replace an existing identity of the same name")
	return cmd
}

func newIdentityListCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:     "list",
		Aliases: []string{"ls"},
		Short:   "List configured identities",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			cfg, err := config.Load()
			if err != nil {
				return reportCoded(aud, err)
			}
			names := sortedKeys(cfg.Identities)
			items := make([]map[string]any, 0, len(names))
			for _, n := range names {
				id := cfg.Identities[n]
				envs := sortedKeys(id.Environments)
				items = append(items, map[string]any{
					"name":         n,
					"type":         id.Type,
					"environments": envs,
				})
			}
			aud.Render(ux.NewPage(items, ""))
			return nil
		},
	}
}

func newIdentityDeleteCmd(_ *app) *cobra.Command {
	return newResourceDeleteCmd(deleteSpec{
		resource: "identity",
		exists: func(cfg *config.Config, name string) bool {
			_, ok := cfg.Identities[name]
			return ok
		},
		references: contextsReferencingIdentity,
		remove: func(cfg *config.Config, name string) {
			delete(cfg.Identities, name)
		},
		materialRefs: identityMaterialRefs,
	})
}

// identityMaterialRefs returns the on-disk secret refs (<identity, env> pairs)
// whose key/token/apikey files become orphaned when the identity `name` is
// deleted. An identity holds one keypair+token+apikey per environment it was
// registered in (Identity.Environments), so we purge each. There is no material
// without an environment, so an identity with no environments yields no purge
// refs (F8-34).
func identityMaterialRefs(cfg *config.Config, name string) []auth.IdentityRef {
	ident, ok := cfg.Identities[name]
	if !ok {
		return nil
	}
	refs := make([]auth.IdentityRef, 0, len(ident.Environments))
	for env := range ident.Environments {
		refs = append(refs, auth.IdentityRef{Identity: name, Environment: env})
	}
	return refs
}

// contextsReferencingIdentity returns the names of contexts that bind ident.
func contextsReferencingIdentity(cfg *config.Config, ident string) []string {
	var out []string
	for name, ctx := range cfg.Contexts {
		if ctx.Identity == ident {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}
