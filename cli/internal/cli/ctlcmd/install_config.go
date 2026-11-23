package ctlcmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/binder"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl/generated"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// writeInstallConfig renders the install draft, applies any schema-driven
// overlay, writes the resulting configuration file with the appropriate mode,
// and returns its absolute path.
func (a *app) writeInstallConfig(
	cmd *cobra.Command,
	opts *installOptions,
	draft *install.Draft,
) (string, error) {
	data, err := draft.Render()
	if err != nil {
		return "", err
	}

	// Additive schema-driven layer (plan.md Phase 6, impl/6.0 §3–§4): overlay the
	// resolved preset + explicit --<section>-<field> flags onto the wizard-rendered
	// config. This is a no-op unless the operator selected a preset or set at least
	// one schema flag, so a plain `install` stays byte-identical (golden contract).
	// The overlay preserves unknown keys/comments (yaml.Node merge), so an
	// enterprise-extended or hand-edited config survives.
	data, err = a.applySchemaOverlay(cmd, opts, data)
	if err != nil {
		return "", err
	}

	out, err := filepath.Abs(opts.out)
	if err != nil {
		return "", err
	}
	if dir := filepath.Dir(out); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil { //nolint:gosec // config dir may be outside ~/.jentic by user choice
			return "", fmt.Errorf("create %s: %w", dir, err)
		}
	}
	// Config contains freshly generated secrets; restrict permissions. The
	// Docker path relaxes to 0644 because the file is bind-mounted into
	// containers running as the unprivileged uid 10001 (#992) — host-side
	// protection comes from ~/.jentic being 0700. Only relax under the managed
	// state dir: a user-chosen --out elsewhere has no such protective parent.
	mode := os.FileMode(0o600)
	if draft.IsDocker() && strings.HasPrefix(out, a.Paths.Dir()+string(filepath.Separator)) {
		mode = 0o644
	}
	if err := os.WriteFile(out, data, mode); err != nil {
		return "", fmt.Errorf("write %s: %w", out, err)
	}
	// WriteFile does not chmod an existing file, and a prior install wrote it
	// 0600 — set the mode explicitly so a reinstall heals old installs too.
	if err := os.Chmod(out, mode); err != nil {
		return "", fmt.Errorf("chmod %s: %w", out, err)
	}
	if draft.IsDocker() && mode == 0o600 {
		fmt.Fprintln(a.Out, theme.Warnf(
			"config written outside %s with mode 0600 — the app container (uid 10001) "+
				"may not be able to read it; chmod 644 %s if the stack fails to start",
			a.Paths.Dir(), out))
	}
	return out, nil
}

// applySchemaOverlay layers the schema-driven config (preset + explicit
// --<section>-<field> flags) onto the wizard-rendered YAML (impl/6.0 §3–§4). It
// returns rendered UNCHANGED when neither a preset nor any schema flag was
// supplied, so the default install path is byte-for-byte what it was before this
// feature — the golden CLI contract depends on that. When something is supplied,
// it resolves the defaults<preset<flags ladder and merges only the resulting
// leaves into the document via a yaml.Node overlay that preserves unknown keys
// and comments (enterprise extension sections, hand edits).
//
// NOTE the overlay deliberately does NOT re-inject schema DEFAULTS: the wizard's
// Render() already emits the values the backend needs, and blanketing every
// schema default on top would fight the wizard's own choices. Only the preset and
// the operator's explicit flags — the layers that express INTENT beyond the
// wizard — are overlaid.
func (a *app) applySchemaOverlay(cmd *cobra.Command, opts *installOptions, rendered []byte) ([]byte, error) {
	overrides, err := binder.ChangedOverrides(cmd, opts.backendCfg)
	if err != nil {
		return nil, err
	}

	// Optional schema-derived interactive config screen (impl/6.1), opt-in and
	// interactive-only so the shipped wizard flow is untouched by default. Its
	// answers become the highest-precedence override layer.
	formOverrides, err := a.runConfigForm(cmd, opts)
	if err != nil {
		return nil, err
	}

	if opts.preset == "" && len(overrides) == 0 && len(formOverrides) == 0 {
		return rendered, nil // fast path: nothing schema-driven requested
	}

	// Compose the intentful layers only (preset < flags < form), NOT schema
	// defaults — the wizard's Render() already emits the backend's needed values,
	// and blanketing every default on top would fight the wizard's own choices.
	settings := ctl.Settings{}
	if opts.preset != "" {
		p, err := ctl.PresetSettings(opts.preset)
		if err != nil {
			return nil, err
		}
		settings = p
	}
	if len(overrides) > 0 {
		ctl.MergeSettings(settings, overrides)
	}
	if len(formOverrides) > 0 {
		ctl.MergeSettings(settings, formOverrides)
	}

	merged, err := ctl.OverlaySettings(rendered, settings)
	if err != nil {
		return nil, err
	}
	fmt.Fprintln(a.Out, theme.Dimf("Applied schema-driven config overlay (preset=%q, %d flag override(s)).", opts.preset, countLeaves(overrides)+countLeaves(formOverrides)))
	return merged, nil
}

// runConfigForm shows the schema-derived interactive config screen when
// --config-form was passed and we have a TTY (impl/6.1). The form binds a fresh
// BackendConfig; the operator fills in any sections they want to override, and
// the non-sensitive, non-empty leaves are returned as a nested override map that
// joins the overlay at the highest precedence. A no-op returning nil when the
// flag is off or there is no terminal.
//
// The form is NOT pre-filled from the resolved defaults: the generated struct's
// UnmarshalJSON enforces required sections, so hydrating it from a partial
// settings map is not reliable — instead the operator sees empty inputs and only
// what they type becomes an override, which is also the least surprising
// "review/adjust" semantics (blank = leave the wizard's value alone).
func (a *app) runConfigForm(cmd *cobra.Command, opts *installOptions) (ctl.Settings, error) {
	if !opts.configForm || !wantsInteractive(cmd, false) {
		return nil, nil
	}
	sensitive, err := ctl.SensitivePaths()
	if err != nil {
		return nil, err
	}
	formCfg := &generated.BackendConfig{}
	excluded := excludeConsentOwned(sensitive, formCfg)
	form := binder.BuildDynamicForm(formCfg, binder.FormOptions{Exclude: excluded})
	if err := install.RunForm(form); err != nil {
		return nil, fmt.Errorf("config form: %w", err)
	}
	// Only leaves the operator actually set (non-zero) become overrides.
	return ctl.Settings(binder.NonZeroOverrides(formCfg, excluded)), nil
}

// excludeConsentOwned extends the sensitive-path Exclude set with every leaf of
// the telemetry section. The telemetry block is owned end-to-end by the
// dedicated consent flow (prompt → stampTelemetryDecision → render, which also
// stamps instance_id and host_os): a schema-overlay flag or form field that
// flipped `telemetry.enabled` after rendering would bypass consent and ship an
// enabled config with no instance id and no host_os stamp — under the
// recommended Docker install the backend would then misreport the container's
// OS, exactly the failure the host_os stamp exists to prevent.
func excludeConsentOwned(sensitive map[string]bool, target interface{}) map[string]bool {
	out := make(map[string]bool, len(sensitive)+4)
	for path := range sensitive {
		out[path] = true
	}
	for _, path := range binder.LeafPaths(target) {
		if path == "telemetry" || strings.HasPrefix(path, "telemetry.") {
			out[path] = true
		}
	}
	return out
}

// countLeaves returns the number of scalar leaves in a nested override map, for a
// human-friendly overlay summary line.
func countLeaves(m map[string]any) int {
	n := 0
	for _, v := range m {
		if sub, ok := v.(map[string]any); ok {
			n += countLeaves(sub)
			continue
		}
		n++
	}
	return n
}
