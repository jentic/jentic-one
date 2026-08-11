// Package clictx is the CLI-only leaf that bridges the UX-free SDK config
// (client/config) to the Cobra command layer. It exists as its own package to
// break an import cycle: the root interceptor (impl/3.2) and the client helpers
// (impl/4.2) both need the resolved ActiveState, but neither may live in client/
// (SDK-clean) nor pull in the whole command tree.
//
// Responsibilities that live strictly HERE, never in client/ (impl/3.2 "adapter
// boundary"):
//  1. Mode/Theme interpretation — the SDK carries only raw PersistedMode/Theme
//     strings; ResolveActiveState applies flag overrides + the precedence ladder.
//  2. The pre-activation legacy-read adapter — until `jentic migrate` reaches
//     end-of-life, a user with no XDG config still resolves state from the legacy
//     ~/.jentic profile store, so wrapping commands in the Audience is a no-op for
//     existing users (plan Phase 2 item 1, 16 §5, 14 BC-1).
//  3. State injection into the Cobra context (WithActiveState/FromContext).
package clictx

import (
	"context"
	"os"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	legacyconfig "github.com/jentic/jentic-one/cli/internal/config"
)

// Canonical mode strings (14 BC-9; mirrored in client/config Context.Mode docs).
const (
	ModeHuman          = "human"
	ModeAgent          = "agent"
	ModeServiceAccount = "service-account"
)

// LegacyEnvironment is the sentinel EnvironmentName the legacy-read adapter
// stamps on state resolved from the ~/.jentic profile store (loadLegacyState).
// Command code uses it to tell "a real V2 context is active" apart from "this
// state is a compatibility view of the V1 store" — the V2 auth bridge
// (api.activeState) must NOT treat the latter as an XDG identity, or it would
// look for keys/tokens under a (profile, "legacy") stem that never existed.
const LegacyEnvironment = "legacy"

// ActiveState is the CLI's resolved view of the world: the SDK's UX-free
// ResolvedState plus the CLI-only Mode/ThemeName the SDK deliberately leaves
// uninterpreted. It embeds *ResolvedState so command code reads BaseURL/identity
// through the same value.
type ActiveState struct {
	*sdkconfig.ResolvedState

	// Mode is the resolved canonical mode ("human"/"agent"/"service-account").
	Mode string
	// ModeExplicit records whether Mode came from an explicit source (--mode,
	// $JENTIC_MODE, or the persisted context mode) rather than the ladder's
	// human default. Output rendering uses it (UX-5): an EXPLICIT human mode
	// pins pretty rendering even when stdout is piped, while default-human
	// keeps the agent-friendly non-TTY→JSON heuristic.
	ModeExplicit bool
	// ThemeName is the resolved config theme name fed to theme.ResolveTheme (the
	// --theme/JENTIC_THEME overrides are applied by the theme resolver, not here).
	ThemeName string
}

// ResolveActiveState loads state and layers the CLI mode/theme interpretation on
// top. contextOverride is --context (may be ""); modeOverride is --mode (may be "").
//
// Resolution order:
//  1. client/config.LoadState (env-var file-less path, else XDG config.yaml).
//  2. On "no XDG config" ONLY, fall back to the legacy ~/.jentic adapter so
//     unmigrated users keep working. Any other LoadState error propagates.
//  3. Apply the mode ladder: --mode > $JENTIC_MODE > persisted > human.
func ResolveActiveState(contextOverride, modeOverride string) (*ActiveState, error) {
	rs, err := sdkconfig.LoadState(contextOverride)
	if err != nil {
		legacy, ok := loadLegacyState()
		if !ok {
			return nil, err // no XDG config AND no legacy config: surface the original error
		}
		rs = legacy
	}

	mode, explicit := ResolveModeExplicit(modeOverride, rs.PersistedMode)
	return &ActiveState{
		ResolvedState: rs,
		Mode:          mode,
		ModeExplicit:  explicit,
		ThemeName:     rs.PersistedTheme,
	}, nil
}

// ResolveMode implements the mode ladder (impl/1.2 §resolveMode, impl/3.2 §2):
// --mode > $JENTIC_MODE > persisted-context-mode > human. There is deliberately
// NO TTY rung — mode is an explicit choice, and an unknown value is validated
// (fail-closed to agent) at UX construction in the root interceptor, not here.
//
// Exported so the root interceptor can re-apply the same ladder on its
// state-resolution FALLBACK path: when there is no config at all, mode must still
// honor --mode/$JENTIC_MODE. Fencing is a safety control — it must never silently
// degrade to unfenced human just because config resolution failed.
func ResolveMode(flagOverride, persisted string) string {
	mode, _ := ResolveModeExplicit(flagOverride, persisted)
	return mode
}

// ResolveModeExplicit is ResolveMode plus whether the result came from an
// explicit rung (flag/env/persisted) or the ladder's human default. Rendering
// needs the distinction (UX-5): explicit human pins pretty output in pipes,
// default human keeps the non-TTY→JSON heuristic.
func ResolveModeExplicit(flagOverride, persisted string) (mode string, explicit bool) {
	if flagOverride != "" {
		return flagOverride, true
	}
	if env := os.Getenv("JENTIC_MODE"); env != "" { // reserved: 14 BC-9
		return env, true
	}
	if persisted != "" {
		return persisted, true
	}
	return ModeHuman, false
}

// loadLegacyState reads the legacy ~/.jentic profile store and maps it onto the
// SDK ResolvedState shape (read-only; the adapter never writes the legacy tree).
// Returns ok=false when no legacy config file exists, so the caller can surface
// the original "no configuration" error rather than a silent empty state.
//
// The legacy store has no mode/theme concept, so those are left empty — the mode
// ladder resolves them to human/default, which is exactly V1's behavior. This is
// the pre-activation compatibility shim; it retires with `jentic migrate`'s
// end-of-life (14 BC-1).
func loadLegacyState() (*sdkconfig.ResolvedState, bool) {
	paths, err := legacyconfig.NewPaths()
	if err != nil {
		return nil, false
	}
	fc, err := legacyconfig.Load(paths)
	if err != nil || fc == nil || !fc.Loaded {
		return nil, false
	}
	return &sdkconfig.ResolvedState{
		IdentityName:    fc.ResolvedDefaultProfile(),
		EnvironmentName: LegacyEnvironment,
		BaseURL:         fc.ResolvedBaseURL(),
		// PersistedMode/PersistedTheme intentionally empty -> human/default.
	}, true
}

type contextKey string

const activeStateKey contextKey = "jentic_active_state"

// WithActiveState stores the resolved state in the Cobra context.
func WithActiveState(ctx context.Context, s *ActiveState) context.Context {
	return context.WithValue(ctx, activeStateKey, s)
}

// FromContext retrieves the ActiveState, or nil if the root interceptor did not
// run (callers that need it should treat nil as a wiring error).
func FromContext(ctx context.Context) *ActiveState {
	s, _ := ctx.Value(activeStateKey).(*ActiveState)
	return s
}
