package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMutateConfig_NoTempResidueOnRenameFailure(t *testing.T) {
	withEnvDirs(t)
	cfgDir, err := ConfigDir()
	if err != nil {
		t.Fatalf("ConfigDir: %v", err)
	}

	err = MutateConfig(func(c *Config) error {
		c.Theme = "dark"
		// Make config.yaml a directory before the atomic rename so os.Rename
		// fails after the temp file has been written.
		return os.MkdirAll(filepath.Join(cfgDir, "config.yaml"), 0o700)
	})
	if err == nil {
		t.Fatal("expected MutateConfig to fail when config.yaml is a directory")
	}

	entries, err := os.ReadDir(cfgDir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") {
			t.Errorf("temp file residue left behind after failed rename: %s", e.Name())
		}
	}
}
