package api

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// meBodyServer answers GET /me with a fixed body.
func meBodyServer(t *testing.T, body string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/me" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(body))
	}))
}

func TestGetMeDecodesPermissions(t *testing.T) {
	srv := meBodyServer(t, `{"type":"agent","id":"agnt_1","name":"a","status":"active",`+
		`"permissions":["apis:read"],"token_permissions":["apis:read"],`+
		`"toolkit_bindings":[],"credential_bindings":[]}`)
	defer srv.Close()

	me, err := testApp(t).getMe(activeCtx(srv.URL))
	if err != nil {
		t.Fatalf("getMe: %v", err)
	}
	if len(me.Permissions) != 1 || me.Permissions[0] != "apis:read" {
		t.Errorf("Permissions = %v, want [apis:read]", me.Permissions)
	}
}

// An older server spells the grant list `scopes`. Decoding it leniently would
// report an agent holding nothing, so getMe must refuse with a coded error that
// names the version skew.
func TestGetMeRejectsPreRenameServer(t *testing.T) {
	srv := meBodyServer(t, `{"type":"agent","id":"agnt_1","name":"a","status":"active",`+
		`"scopes":["apis:read"],"token_scopes":["apis:read"],`+
		`"toolkit_bindings":[],"credential_bindings":[]}`)
	defer srv.Close()

	_, err := testApp(t).getMe(activeCtx(srv.URL))
	var ce *ux.CodedError
	if !errors.As(err, &ce) {
		t.Fatalf("getMe error = %v, want a *ux.CodedError", err)
	}
	if !strings.Contains(ce.Msg, "older release") {
		t.Errorf("Msg = %q, want it to name the older server release", ce.Msg)
	}
}
