package api

import (
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// exactNamedArgs is a cobra Args validator that requires exactly the named
// positional arguments and, on a miscount, returns a coded MISSING_ARGUMENT that
// NAMES the expected arguments plus the corrected invocation (UX-22 / AGT-20) —
// instead of cobra's bare "accepts 1 arg(s), received 0". `use` is the command's
// canonical usage string (e.g. "execute <operation-id | METHOD url>"). The error
// is coded so an agent gets a closed error_code + exit 1 and a human gets a
// styled, actionable line (decorateCodedErrors renders it through the Audience).
func exactNamedArgs(use string, names ...string) cobra.PositionalArgs {
	return func(cmd *cobra.Command, args []string) error {
		if len(args) == len(names) {
			return nil
		}
		return &ux.CodedError{
			Code: ux.CodeMissingArgument,
			Msg: fmt.Sprintf("%s expects %d argument(s) (%s) but got %d",
				cmd.CommandPath(), len(names), strings.Join(names, ", "), len(args)),
			Actionable: fmt.Sprintf("Usage: %s %s", cmd.CommandPath(), use),
		}
	}
}

// rangeNamedArgs is exactNamedArgs for a variable count: it requires min..max
// positional args and, on a miscount, names them + the usage line as a coded
// MISSING_ARGUMENT. Used by `apis rm` (1–2 args: name, optional version).
func rangeNamedArgs(minArgs, maxArgs int, use string, names ...string) cobra.PositionalArgs {
	return func(cmd *cobra.Command, args []string) error {
		if len(args) >= minArgs && len(args) <= maxArgs {
			return nil
		}
		return &ux.CodedError{
			Code: ux.CodeMissingArgument,
			Msg: fmt.Sprintf("%s expects %d–%d argument(s) (%s) but got %d",
				cmd.CommandPath(), minArgs, maxArgs, strings.Join(names, ", "), len(args)),
			Actionable: fmt.Sprintf("Usage: %s %s", cmd.CommandPath(), use),
		}
	}
}

// mustMarkRequired marks a flag required, panicking on the only failure mode
// (the flag does not exist) — a wiring bug we want surfaced loudly at startup,
// not silently ignored (impl/1.3 §3 lint policy: pick one policy, apply it).
func mustMarkRequired(cmd *cobra.Command, name string) {
	if err := cmd.MarkFlagRequired(name); err != nil {
		panic("mustMarkRequired: " + err.Error())
	}
}
