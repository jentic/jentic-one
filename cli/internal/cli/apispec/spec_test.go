package apispec

import (
	"net/http"
	"testing"

	"github.com/jentic/jentic-one/cli/client/generated/control"
)

// TestParseEmbeddedControlSpec parses the real vendored control spec and exercises
// path matching against a couple of known routes, guarding the libopenapi wiring.
func TestParseEmbeddedControlSpec(t *testing.T) {
	spec, err := Parse(control.SpecYAML)
	if err != nil {
		t.Fatalf("Parse embedded control spec: %v", err)
	}
	if spec.Version == "" {
		t.Error("expected a spec info.version")
	}

	// A static route.
	if _, ok := spec.Match("GET", "/credentials"); !ok {
		t.Error("GET /credentials should match")
	}
	// A templated route with a concrete id.
	if _, ok := spec.Match("GET", "/credentials/cred_123"); !ok {
		t.Error("GET /credentials/{credential_id} should match a concrete id")
	}
	// Static route beats the templated sibling: /credentials/providers must
	// resolve to listProviders, not getCredential.
	if op, ok := spec.Match("GET", "/credentials/providers"); !ok || op.OperationID != "listProviders" {
		t.Errorf("GET /credentials/providers should resolve to listProviders, got %+v (ok=%v)", op, ok)
	}
	// Query strings are ignored for matching.
	if _, ok := spec.Match("GET", "/credentials?limit=10"); !ok {
		t.Error("query string should not defeat matching")
	}
	// A bogus route must not match.
	if _, ok := spec.Match("GET", "/definitely/not/a/route"); ok {
		t.Error("bogus route should not match")
	}
	// HasPath distinguishes wrong-method from no-route.
	if !spec.HasPath("/credentials") {
		t.Error("HasPath(/credentials) should be true")
	}
}

func TestListAndDescribe(t *testing.T) {
	spec, err := Parse(control.SpecYAML)
	if err != nil {
		t.Fatal(err)
	}
	ops := spec.List("credential")
	if len(ops) == 0 {
		t.Fatal("expected some credential operations")
	}
	for _, op := range ops {
		if op.Method == "" || op.Path == "" {
			t.Errorf("op missing method/path: %+v", op)
		}
	}

	d, ok := spec.Describe("GET", "/credentials")
	if !ok {
		t.Fatal("describe GET /credentials failed")
	}
	if d.Path != "/credentials" || d.Method != http.MethodGet {
		t.Errorf("describe = %+v", d)
	}
}
