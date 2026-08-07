package binder_test

import (
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/binder"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl/generated"
)

// nested mirrors the shape go-jsonschema emits for BackendConfig: sections are
// (pointer) sub-structs, leaves are scalars at varying depth, collections are
// present to prove they are skipped.
type leafSection struct {
	Host string `json:"host"`
	Port int    `json:"port"`
}

type deepSection struct {
	Registry leafSection `json:"registry"`
}

type nested struct {
	Server   leafSection    `json:"server"`
	Database *deepSection   `json:"database,omitempty"`
	Logging  *logSection    `json:"logging,omitempty"`
	Apps     []string       `json:"apps,omitempty"`  // collection: skipped
	Extra    map[string]int `json:"extra,omitempty"` // map: skipped
	Ignored  string         `json:"-"`               // json-ignored: skipped
}

type logSection struct {
	Level   string  `json:"level"`
	Enabled bool    `json:"enabled"`
	Ratio   float64 `json:"ratio"`
}

func TestBindFlags_FlattensNestedScalarLeavesToDottedFlags(t *testing.T) {
	cmd := &cobra.Command{Use: "install"}
	binder.BindFlags(cmd, &nested{})
	flags := cmd.Flags()

	for _, name := range []string{
		"server-host", "server-port",
		"database-registry-host", "database-registry-port",
		"logging-level", "logging-enabled", "logging-ratio",
	} {
		if flags.Lookup(name) == nil {
			t.Errorf("expected flag --%s to be registered", name)
		}
	}

	// Collections / ignored fields must NOT produce flags.
	for _, name := range []string{"apps", "extra", "ignored"} {
		if flags.Lookup(name) != nil {
			t.Errorf("collection/ignored field must not register a flag: --%s", name)
		}
	}
}

func TestHydrateStruct_OnlyOverridesChangedFlags(t *testing.T) {
	cmd := &cobra.Command{Use: "install"}
	cfg := &nested{}
	binder.BindFlags(cmd, cfg)

	// Simulate the precedence ladder: defaults/preset populated cfg first.
	cfg.Server.Host = "preset-host"
	cfg.Server.Port = 1111
	cfg.Logging = &logSection{Level: "info", Enabled: false}

	// Operator passes only a subset of flags.
	if err := cmd.ParseFlags([]string{
		"--server-port=8080",
		"--database-registry-host=db.internal",
		"--logging-enabled=true",
		"--logging-ratio=0.5",
	}); err != nil {
		t.Fatalf("ParseFlags: %v", err)
	}
	if err := binder.HydrateStruct(cmd, cfg); err != nil {
		t.Fatalf("HydrateStruct: %v", err)
	}

	// Changed flags win.
	if cfg.Server.Port != 8080 {
		t.Errorf("server.port = %d, want 8080 (flag override)", cfg.Server.Port)
	}
	if cfg.Database == nil || cfg.Database.Registry.Host != "db.internal" {
		t.Errorf("database.registry.host not hydrated: %+v", cfg.Database)
	}
	if cfg.Logging.Enabled != true || cfg.Logging.Ratio != 0.5 {
		t.Errorf("logging leaves not hydrated: %+v", cfg.Logging)
	}
	// Unchanged flag must NOT clobber the preset value with a zero.
	if cfg.Server.Host != "preset-host" {
		t.Errorf("server.host = %q, want preset-host preserved (flag not set)", cfg.Server.Host)
	}
	if cfg.Logging.Level != "info" {
		t.Errorf("logging.level = %q, want info preserved (flag not set)", cfg.Logging.Level)
	}
}

func TestBindFlags_UnsupportedKindPanicsLoudly(t *testing.T) {
	type bad struct {
		Ch chan int `json:"ch"`
	}
	defer func() {
		if recover() == nil {
			t.Error("unsupported kind must panic at construction, not be silently skipped")
		}
	}()
	binder.BindFlags(&cobra.Command{Use: "x"}, &bad{})
}

func TestBindFlagsWithOptions_ExcludesAndHides(t *testing.T) {
	cmd := &cobra.Command{Use: "install"}
	binder.BindFlagsWithOptions(cmd, &nested{}, binder.BindOptions{
		Exclude: map[string]bool{"server.host": true},
		Hidden:  true,
	})
	// Excluded leaf must not be registered at all.
	if cmd.Flags().Lookup("server-host") != nil {
		t.Error("excluded leaf --server-host must not be registered")
	}
	// A non-excluded leaf must be registered but hidden.
	f := cmd.Flags().Lookup("server-port")
	if f == nil {
		t.Fatal("expected --server-port to be registered")
	}
	if !f.Hidden {
		t.Error("--server-port should be hidden when BindOptions.Hidden is set")
	}
}

func TestBindFlags_RejectsNonStructPointer(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Error("BindFlags must panic on a non-struct-pointer target")
		}
	}()
	x := 5
	binder.BindFlags(&cobra.Command{Use: "x"}, &x)
}

// The real generated BackendConfig must bind cleanly (no panic) and stay well
// under the recursion bound — the whole point of the phase.
func TestBindFlags_RealBackendConfigBindsCleanly(t *testing.T) {
	cmd := &cobra.Command{Use: "install"}
	binder.BindFlags(cmd, &generated.BackendConfig{})

	// A representative spread of dotted leaves the real schema is known to carry.
	for _, name := range []string{
		"server-port",
		"logging-file-enabled",
		"databases-control-host",
	} {
		if cmd.Flags().Lookup(name) == nil {
			t.Errorf("expected real-config flag --%s", name)
		}
	}
}

// A pathologically deep (self-referential) shape must trip the depth bound and
// panic rather than overflow the stack.
func TestBindFlags_ExceedingDepthPanics(t *testing.T) {
	type recur struct {
		Next *recur `json:"next,omitempty"`
		Leaf string `json:"leaf"`
	}
	defer func() {
		if recover() == nil {
			t.Error("a cyclic/too-deep struct must panic at the depth bound")
		}
	}()
	binder.BindFlags(&cobra.Command{Use: "x"}, &recur{})
}

func TestChangedOverrides_OnlyIncludesSetFlagsAsNestedMap(t *testing.T) {
	cmd := &cobra.Command{Use: "install"}
	cfg := &nested{}
	binder.BindFlags(cmd, cfg)

	if err := cmd.ParseFlags([]string{
		"--server-port=8080",
		"--database-registry-host=db.internal",
	}); err != nil {
		t.Fatalf("ParseFlags: %v", err)
	}
	over, err := binder.ChangedOverrides(cmd, cfg)
	if err != nil {
		t.Fatalf("ChangedOverrides: %v", err)
	}

	server, ok := over["server"].(map[string]any)
	if !ok || server["port"] != int64(8080) {
		t.Errorf("server.port override missing/wrong: %v", over["server"])
	}
	db, ok := over["database"].(map[string]any)
	if !ok {
		t.Fatalf("database override missing: %v", over)
	}
	reg, ok := db["registry"].(map[string]any)
	if !ok || reg["host"] != "db.internal" {
		t.Errorf("database.registry.host override missing/wrong: %v", db)
	}
	// A flag that was NOT set must be entirely absent (not a zero value).
	if _, present := server["host"]; present {
		t.Errorf("unset flag leaked into overrides: %v", server)
	}
	if _, present := over["logging"]; present {
		t.Errorf("untouched section leaked into overrides: %v", over)
	}
}
