package api

import (
	"reflect"
	"testing"

	"github.com/spf13/cobra"
)

func TestProfileVerbToContext(t *testing.T) {
	cases := []struct {
		verb string
		args []string
		want []string
		ok   bool
	}{
		{"list", nil, []string{"context", "list"}, true},
		{"ls", nil, []string{"context", "list"}, true},
		{"use", []string{"prod"}, []string{"context", "use", "prod"}, true},
		{"view", []string{"prod"}, []string{"context", "view"}, true}, // name arg dropped by design
		{"delete", []string{"prod"}, []string{"context", "delete", "prod"}, true},
		{"add-key", []string{"prod"}, nil, false}, // retargets identity, handled separately
		{"bogus", nil, nil, false},
	}
	for _, c := range cases {
		got, ok := profileVerbToContext(c.verb, c.args)
		if ok != c.ok {
			t.Errorf("profileVerbToContext(%q) ok = %v, want %v", c.verb, ok, c.ok)
			continue
		}
		if ok && !reflect.DeepEqual(got, c.want) {
			t.Errorf("profileVerbToContext(%q,%v) = %v, want %v", c.verb, c.args, got, c.want)
		}
	}
}

func TestRemapProfileSelection(t *testing.T) {
	if got := remapProfileSelection("flagval", "envval"); got != "flagval" {
		t.Errorf("flag should win: got %q", got)
	}
	if got := remapProfileSelection("", "envval"); got != "envval" {
		t.Errorf("env fallback: got %q", got)
	}
	if got := remapProfileSelection("", ""); got != "" {
		t.Errorf("neither set should yield empty: got %q", got)
	}
}

// TestProfileAliasShimsDormant proves the alias layer is NOT wired into the live
// tree today (16 §1: breaking-half code stays gated until activation). The real
// V1 profile command owns the "profile" name; registering the shim is an
// activation-release swap, not a merge-time change.
func TestProfileAliasShimsDormant(t *testing.T) {
	app := testApp(t)
	root := newAPIRootCmd(app.App)
	profileCmd, _, err := root.Find([]string{"profile"})
	if err != nil {
		t.Fatalf("profile command missing: %v", err)
	}
	// The live profile command is the V1 one (not hidden). If this ever flips to
	// Hidden, the activation swap has happened and this test should be updated.
	if profileCmd.Hidden {
		t.Error("profile command is hidden — the alias shim appears to be wired live before activation")
	}
	// And it still has its V1 subcommands (list/use/add-key), not the shim's
	// ArbitraryArgs delegation.
	if len(profileCmd.Commands()) == 0 {
		t.Error("live profile command should retain its V1 subcommands until activation")
	}

	// The activation entry point exists and produces a HIDDEN shim (kept ready
	// for the one-line activation swap), but is NOT attached to the live tree
	// above. Constructing it on a throwaway root proves the shim shape without
	// wiring it live.
	shimRoot := &cobra.Command{Use: "jentic"}
	registerProfileAliasShims(shimRoot, app)
	shim, _, err := shimRoot.Find([]string{"profile"})
	if err != nil {
		t.Fatalf("shim profile not attached to throwaway root: %v", err)
	}
	if !shim.Hidden {
		t.Error("alias shim profile command should be hidden")
	}
}
