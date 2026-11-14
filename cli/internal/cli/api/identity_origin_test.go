package api

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

func TestIdentityAdd_UnresolvableOriginDoesNotCommit(t *testing.T) {
	for _, tc := range []struct {
		name    string
		baseURL string
	}{
		{name: "empty base URL", baseURL: ""},
		{name: "unresolvable base URL", baseURL: "://bad"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
				cfg.Environments["prod"] = sdkconfig.Env{BaseURL: tc.baseURL}
				return nil
			}); err != nil {
				t.Fatal(err)
			}

			const key = "jak_identity_origin_test" // pragma: allowlist secret
			err := runJentic(t, "identity", "add", "ci", "--type", "user", "--env", "prod", "--api-key", key)
			if err == nil || !strings.Contains(err.Error(), "cannot bind API key") {
				t.Fatalf("identity add with unresolvable origin = %v, want a binding error", err)
			}
			cfg, err := sdkconfig.Load()
			if err != nil {
				t.Fatal(err)
			}
			if _, exists := cfg.Identities["ci"]; exists {
				t.Error("identity add committed an identity without a bound credential")
			}
			stateDir, err := sdkconfig.StateDir()
			if err != nil {
				t.Fatal(err)
			}
			keyPath := filepath.Join(stateDir, "ci_prod.apikey")
			if _, err := os.Stat(keyPath); !os.IsNotExist(err) {
				t.Errorf("identity add wrote an origin-less key: %v", err)
			}

			if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
				cfg.Environments["prod"] = sdkconfig.Env{BaseURL: "https://origin-a.example.com/v1"}
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			if err := runJentic(t, "identity", "add", "ci", "--type", "user", "--env", "prod", "--api-key", key); err != nil {
				t.Fatalf("retry without --force after correcting the origin: %v", err)
			}
			ref := auth.IdentityRef{Identity: "ci", Environment: "prod"}
			if got, err := auth.ReadAPIKey(ref); err != nil || got != key {
				t.Fatalf("ReadAPIKey after retry = %q, %v", got, err)
			}
			originData, err := os.ReadFile(keyPath + ".origin")
			if err != nil {
				t.Fatal(err)
			}
			if got := string(originData); got != "https://origin-a.example.com" {
				t.Errorf("stored origin = %q, want the corrected environment origin", got)
			}
		})
	}
}
