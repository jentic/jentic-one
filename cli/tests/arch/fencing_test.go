package arch

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/pkg/clitree"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// Test1C_SecurityFencing is the guardrail that every command in the canonical
// fence set (clitree.MustBeFenced, impl/3.2 §2a) carries the `fenced` annotation,
// so an autonomous agent cannot hijack the operator's local-agent lifecycle,
// contexts, or identities. It also asserts cobra.EnableTraverseRunHooks is set,
// because without it a subcommand's own PersistentPreRunE would silently disable
// the fencing hook for its whole subtree.
//
// This test walks the constructed command tree and asserts the contract;
// commands added to clitree.MustBeFenced are covered automatically.
func Test1C_SecurityFencing(t *testing.T) {
	if len(clitree.MustBeFenced) == 0 {
		t.Fatal("clitree.MustBeFenced is empty; the canonical fence set must list every host-mutating command")
	}

	// Build the real jentic tree the same way the binary does.
	root := clitree.API()(&core.AppContainer{})

	for _, path := range clitree.MustBeFenced {
		fields := strings.Fields(path) // "context use" -> ["context","use"]
		cmd, _, err := root.Find(fields)
		if err != nil {
			t.Errorf("fenced command %q not found in the jentic tree: %v", path, err)
			continue
		}
		// root.Find returns the nearest match; confirm we resolved the exact leaf,
		// not a parent (e.g. a typo'd path that resolves to root).
		if cmd.Name() != fields[len(fields)-1] {
			t.Errorf("fenced path %q resolved to %q, not the intended leaf", path, cmd.CommandPath())
			continue
		}
		if cmd.Annotations["fenced"] != "true" {
			t.Errorf("command %q is NOT fenced; agent mode could run it. Wrap its constructor in fenced(...)", path)
		}
	}

	// The fencing hook lives on the parent root; a child PersistentPreRunE would
	// bypass it unless traversal is enabled (impl/3.2 §2 "COBRA HAZARD").
	if !cobra.EnableTraverseRunHooks {
		t.Error("cobra.EnableTraverseRunHooks is false; a subcommand's own PersistentPreRunE would silently disable fencing for its subtree")
	}
}

// fencingExemptPrefixes is the reviewed set of `jentic` command paths (or path
// prefixes) that are NOT fenced ON PURPOSE — the carve-outs the fencing doctrine
// on clitree.MustBeFenced describes. It is the second half of the fencing guard:
// direction (b) below walks the WHOLE tree and requires every runnable leaf to be
// either fenced (MustBeFenced) or covered by an entry here. A NEW command cannot
// ship silently unfenced — the author must consciously fence it or justify the
// carve-out here.
//
// A leaf is exempt if its path (binary stripped) equals an entry OR begins with
// "<entry> " (so "apis" exempts the whole read-only+data-plane apis subtree).
//
// DOCTRINE (clitree.MustBeFenced): fencing protects the operator's HOST-level
// management state (contexts/envs/identities/local-agent lifecycle) and prevents
// revealing/switching to a non-active context. It does NOT gate the data plane:
// server-side scope + OS isolation are the real boundary there, so the agent's
// own provisioned API operations (search/execute/apis/catalog/access/credentials/
// history/events/api) are deliberately reachable. Anything that mutates the data
// plane is still authorized server-side against the agent's scope.
var fencingExemptPrefixes = []string{
	// Read-only management views (active context only). NOTE: `context list` is
	// intentionally NOT here — it is fenced (it enumerates the operator's OTHER
	// identities/contexts on a shared machine); see clitree.MustBeFenced.
	"context view", "env list", "identity list",
	// Agent self-service + migration (BC-1 directs agents to run migrate).
	"identity register", "migrate",
	// Local preference, not a context/management switch.
	"theme",
	// Data-plane / discovery subtrees — the point of the agent surface. Any
	// mutation here is server-side-scope-authorized, not host management.
	"search", "execute", "inspect", "endpoints",
	"apis", "catalog", "api", "history", "events", "credentials", "access",
	// Agent-facing self-check (read-only) and login/logout of the agent's own
	// session (not an operator context switch).
	"doctor", "whoami", "login", "logout",
	// The MCP stdio server: serves the agent's data-plane surface to a local
	// MCP client. Read-only host-wise (it writes only its own log file), never
	// prompts, never switches or reveals a non-active context; every backend
	// call is server-side-scope-authorized like the data-plane commands above.
	"mcp",
	// Skill self-provisioning + own-identity key management (writes only the
	// agent's own runtime / its own credential, never another identity). NOTE:
	// `setup` is NOT here — it is fenced (AGT-5): it hangs on a human
	// approval poll and writes skill files; agents use `register`.
	"skill", "register",
	// Operator admin config surface (jentic-tree mirror) — reachable to operators;
	// server-side-authorized. (jenticctl is the primary operator binary.)
	"admin",
}

// fencingExempt reports whether a leaf path is a reviewed carve-out.
func fencingExempt(path string) bool {
	for _, p := range fencingExemptPrefixes {
		if path == p || strings.HasPrefix(path, p+" ") {
			return true
		}
	}
	return false
}

// Test1C2_NoUnfencedMutatingCommand is direction (b) of the fencing guard
// (impl/8.1 F8-3): a reverse tree-walk asserting that every runnable leaf in the
// `jentic` command tree is either fenced (MustBeFenced) or an explicitly reviewed
// carve-out (fencingExemptPrefixes). Test1C only checks direction (a) — that the
// listed commands ARE annotated; without (b), a newly-added host-mutating command
// the author forgot to fence would pass CI unnoticed. This closes that gap.
func Test1C2_NoUnfencedMutatingCommand(t *testing.T) {
	root := clitree.API()(&core.AppContainer{})

	fenced := map[string]bool{}
	for _, p := range clitree.MustBeFenced {
		fenced[p] = true
	}

	// stripBin turns "jentic context view" into "context view" (the keys used by
	// MustBeFenced / fencingExemptPrefixes).
	stripBin := func(full string) string {
		parts := strings.Fields(full)
		if len(parts) <= 1 {
			return "" // the bare root
		}
		return strings.Join(parts[1:], " ")
	}

	scanned := 0
	var walk func(cmd *cobra.Command)
	walk = func(cmd *cobra.Command) {
		// A runnable leaf is a command with a Run/RunE and no subcommands. Groups
		// without a Run just print help (harmless).
		if cmd.Runnable() && !cmd.HasSubCommands() {
			if path := stripBin(cmd.CommandPath()); path != "" {
				scanned++
				isFenced := cmd.Annotations["fenced"] == "true" || fenced[path]
				if !isFenced && !fencingExempt(path) {
					t.Errorf("runnable command %q is neither fenced nor an explicit carve-out. "+
						"If it mutates host management state (contexts/envs/identities/lifecycle) or "+
						"reveals a non-active context, add it to clitree.MustBeFenced. If it is read-only/"+
						"agent-safe/data-plane, add it to fencingExemptPrefixes in this test with a reason (F8-3).",
						cmd.CommandPath())
				}
			}
		}
		for _, sub := range cmd.Commands() {
			walk(sub)
		}
	}
	walk(root)

	if scanned == 0 {
		t.Fatal("no runnable leaves scanned — the reverse fencing guard must not pass vacuously")
	}
}
