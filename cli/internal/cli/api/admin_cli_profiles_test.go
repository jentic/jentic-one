package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAdminCLIProfilesProvisionPostsTheProfile(t *testing.T) {
	withXDG(t)
	var path string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.Method + " " + r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"profile":"github","vendor":"github","vendor_enabled":true,"apis":[
			{"role":"graphql","source":"shipped:specs/github-graphql.openapi.yaml","api_id":"github.com/github-graphql",
			"vendor":"github-com","name":"github-graphql","version":"1","status":"imported"}]}`))
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "admin", "cli-profiles", "provision", "github")
	if err != nil {
		t.Fatalf("provision: %v\n%s", err, out)
	}
	if path != "POST /cli-profiles/github:provision" {
		t.Errorf("request = %q", path)
	}
	if !strings.Contains(out, `"imported"`) {
		t.Errorf("output lacks the API status\n%s", out)
	}
}

func TestCLIProfilesListsTheProfiles(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/cli-profiles" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[{"profile":"github","display_name":"GitHub","vendor":"github",
			"vendor_enabled":true,"cli":"gh","credential_mode":"per_api","apis":[],"hard_deny":[],
			"presets":[{"name":"read_only","description":"Read everything.","roles":["rest"],"params":[]}]}]}`))
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "cli", "profiles")
	if err != nil || !strings.Contains(out, "read_only") {
		t.Fatalf("cli profiles: %v\n%s", err, out)
	}
}
