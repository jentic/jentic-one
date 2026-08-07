package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

// TestIdentityRegister_PersistsClientIDAndStatus drives `jentic identity register`
// end-to-end against a mock /register (loopback http is allowed by the transport
// guard). It asserts the returned client_id + status land in config.yaml under the
// active (identity, environment) pair and that an env-scoped key was minted.
func TestIdentityRegister_PersistsClientIDAndStatus(t *testing.T) {
	withXDG(t)

	var gotBody struct {
		ClientName string    `json:"client_name"`
		Jwks       auth.JWKS `json:"jwks"`
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/register" || r.Method != http.MethodPost {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]string{"client_id": "client_abc", "status": "pending"})
	}))
	defer srv.Close()

	if err := runJentic(t, "env", "add", "local", "--url", srv.URL); err != nil {
		t.Fatalf("env add: %v", err)
	}
	if err := runJentic(t, "identity", "add", "agent1", "--type", "agent"); err != nil {
		t.Fatalf("identity add: %v", err)
	}
	if err := runJentic(t, "context", "create", "c1", "--env", "local", "--identity", "agent1"); err != nil {
		t.Fatalf("context create: %v", err)
	}
	if err := runJentic(t, "context", "use", "c1"); err != nil {
		t.Fatalf("context use: %v", err)
	}

	if err := runJentic(t, "identity", "register"); err != nil {
		t.Fatalf("identity register: %v", err)
	}

	if gotBody.ClientName != "agent1" || len(gotBody.Jwks.Keys) != 1 {
		t.Errorf("register request body = %+v", gotBody)
	}

	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	reg, ok := cfg.Identities["agent1"].Environments["local"]
	if !ok {
		t.Fatal("no registration state persisted for agent1/local")
	}
	if reg.ClientID != "client_abc" || reg.Status != "pending" {
		t.Errorf("registration state = %+v, want client_abc/pending", reg)
	}
}

// TestIdentityRegister_RequiresActiveContext: with no active context there is no
// identity/environment to register, so the command fails cleanly.
func TestIdentityRegister_RequiresActiveContext(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "identity", "register"); err == nil {
		t.Fatal("expected register to fail with no active context")
	}
}
