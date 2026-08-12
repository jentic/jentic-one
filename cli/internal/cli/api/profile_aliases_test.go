package api

import (
	"reflect"
	"testing"
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

// TestProfileAliasShimsLive proves the activation swap happened: the V1 profile
// command is GONE and the hidden alias shim owns the "profile" name, delegating
// V1 muscle memory onto the context successors (BC-3) instead of erroring with
// "unknown command".
func TestProfileAliasShimsLive(t *testing.T) {
	app := testApp(t)
	root := newAPIRootCmd(app.App)
	profileCmd, _, err := root.Find([]string{"profile"})
	if err != nil {
		t.Fatalf("profile shim missing from the live tree: %v", err)
	}
	// The live profile command is the shim: hidden, no V1 subcommands, and an
	// ArbitraryArgs delegator.
	if !profileCmd.Hidden {
		t.Error("profile alias shim should be hidden in the live tree")
	}
	if len(profileCmd.Commands()) != 0 {
		t.Errorf("shim should carry no V1 subcommands, got %d", len(profileCmd.Commands()))
	}
	if profileCmd.RunE == nil {
		t.Error("shim should delegate through RunE")
	}
}
