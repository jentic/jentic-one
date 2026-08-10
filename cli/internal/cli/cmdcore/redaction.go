package cmdcore

import (
	"github.com/jentic/jentic-one/cli/client/generated/broker"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// Register both planes' generated SensitiveFields tables (specgen's `x-sensitive`
// output) with the redaction engine (GEN-2). Layer-1 typed redaction reads them
// by type name; without this call the tables are generated but INERT, and the
// day the backend annotates its first field the annotation would silently do
// nothing. cmdcore is the composition point every command tree passes through
// (both binaries and downstream pkg/clitree embedders link it), so an init here
// guarantees registration on every path — including tests that call command
// bodies directly. Idempotent/additive by RegisterSensitiveFields' contract.
//
// tests/arch's TestSensitiveTablesRegistered drift-gates this wiring.
func init() {
	ux.RegisterSensitiveFields(control.SensitiveFields)
	ux.RegisterSensitiveFields(broker.SensitiveFields)
}
