package localagentcmd

import "github.com/jentic/jentic-one/cli/internal/cli/ux"

// coded.go gives the local-agent commands (run/setup/skill) the same coded
// error contract as api/cmdcore (ARCH-23/AGT-24). Before this the package
// returned only raw fmt.Errorf, so a driving agent got exit 1 with no
// error_code for the failures it most needs to branch on — a missing agent
// binary, a grant/account that isn't set up, an unconfinable machine, or a
// cancelled onboarding. These helpers wrap those specific sinks in the closed
// taxonomy; the rest of the package's raw errors stay as-is (they are operator-
// interactive paths where a human reads the prose).
//
// localagentcmd sits above the leaves, so importing ux is allowed by the
// layering gate (the api/cmdcore trees do the same).

// confinementUnavailableErr is the AGT-24 emitter for CONFINEMENT_UNAVAILABLE.
// `run` refuses to
// launch an unconfined session, so a machine without sandbox-exec/bwrap is a
// hard, non-retryable stop the agent must surface distinctly (not a generic
// exit 1). detail carries the per-prereq reasons already assembled by the
// caller.
func confinementUnavailableErr(detail string) error {
	return &ux.CodedError{
		Code:       ux.CodeConfinementMissing,
		Msg:        detail,
		Actionable: "Install the confinement prerequisites (sandbox-exec on macOS, bwrap + unprivileged user namespaces on Linux), or run the agent in isolation another way (e.g. inside Docker).",
	}
}

// binaryMissingErr codes an absent agent binary as RESOLVE_FAILED — the agent
// selected a runtime whose binary isn't installed/provisioned, a "change the
// ask / provision first" state rather than a CLI bug.
func binaryMissingErr(msg string) error {
	return &ux.CodedError{
		Code:       ux.CodeResolveFailed,
		Msg:        msg,
		Actionable: "Install the agent binary (or run `jentic setup` to provision the isolated agent user), then re-run.",
	}
}

// accountMissingErr codes an operation that needs a provisioned agent account
// when none exists as RESOLVE_FAILED with the setup remedy.
func accountMissingErr(msg string) error {
	return &ux.CodedError{
		Code:       ux.CodeResolveFailed,
		Msg:        msg,
		Actionable: "Run `jentic setup` to create the isolated agent user first.",
	}
}
