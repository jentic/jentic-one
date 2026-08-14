package api

import (
	"fmt"
	"log/slog"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// deleteSpec parameterizes the shared delete flow for a top-level config resource
// (environment/identity). The env and identity delete commands differ only in the
// resource noun, the existence check, the referential-integrity check, and the
// map key to delete — everything else (Audience confirmation, MutateConfig,
// Result rendering, --yes) is identical, so it lives here once.
type deleteSpec struct {
	resource string // "environment" / "identity"
	// exists reports whether name is present in cfg.
	exists func(cfg *sdkconfig.Config, name string) bool
	// references returns context names still pointing at name (block deletion).
	references func(cfg *sdkconfig.Config, name string) []string
	// remove deletes name from the appropriate map.
	remove func(cfg *sdkconfig.Config, name string)
	// materialRefs, if non-nil, returns the on-disk secret refs (key/token/apikey
	// stems) that become orphaned once name is removed, so the delete flow can
	// purge them (F8-34). For an identity that is every <identity, env> pair it
	// was registered in; for an environment there is no per-name secret material,
	// so env delete leaves this nil.
	materialRefs func(cfg *sdkconfig.Config, name string) []auth.IdentityRef
}

// newResourceDeleteCmd builds a `delete <name>` command for a top-level resource
// from a deleteSpec. Shared by env delete and identity delete (dedup: the two
// flows are byte-identical apart from the spec).
func newResourceDeleteCmd(spec deleteSpec) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "delete <name>",
		Short: "Delete " + article(spec.resource) + " " + spec.resource,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			aud := ux.FromContext(cmd.Context())

			cfg, err := sdkconfig.Load()
			if err != nil {
				return reportCoded(aud, err)
			}
			if !spec.exists(cfg, name) {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg:  fmt.Sprintf("%s %q does not exist", spec.resource, name),
				})
			}
			if refs := spec.references(cfg, name); len(refs) > 0 {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("%s %q is still referenced by context(s): %v", spec.resource, name, refs),
					Actionable: "Delete or re-point those contexts first.",
				})
			}

			ok, cerr := aud.AskConfirm(fmt.Sprintf("Delete %s %q?", spec.resource, name))
			if cerr != nil {
				return reportCoded(aud, cerr)
			}
			if !ok {
				return nil
			}

			// Capture the orphan-able secret refs BEFORE the config entry is
			// removed (afterwards the environments map is gone). Best-effort: a
			// stem that no longer validates is simply skipped.
			var toPurge []auth.IdentityRef
			if spec.materialRefs != nil {
				toPurge = spec.materialRefs(cfg, name)
			}

			if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
				spec.remove(cfg, name)
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}

			// Remove the now-orphaned on-disk key/token/apikey files (F8-34). The
			// config entry is already gone, so a purge failure must not fail the
			// whole delete (the resource IS deleted); log it as a warning through
			// the mode-appropriate slog handler (stderr, redacted).
			for _, ref := range toPurge {
				if perr := auth.PurgeMaterial(ref); perr != nil {
					slog.Warn("could not remove orphaned identity secret files",
						"identity", ref.Identity, "environment", ref.Environment, "error", perr)
				}
			}

			aud.Render(ux.Result{Status: ux.StatusDeleted, Resource: spec.resource, Name: name})
			return nil
		},
	}
	// --yes is consumed by the root interceptor (Audience assumeYes); bind it so
	// the lookup finds it, without a dead local (F8-23).
	cmd.Flags().BoolP("yes", "y", false, "Skip the confirmation prompt")
	return cmd
}

// article returns "an" for a vowel-initial noun, else "a" — for readable Short
// help ("Delete an environment" / "Delete an identity").
func article(noun string) string {
	if noun == "" {
		return "a"
	}
	switch noun[0] {
	case 'a', 'e', 'i', 'o', 'u':
		return "an"
	default:
		return "a"
	}
}
