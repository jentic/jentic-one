package api

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/profile"
)

// TestProfileListJSON is the AGT-2 regression: `profile list` — the skill's
// "start here" command — must have a machine path. The JSON document carries a
// row per profile with registration/token state and never the API key itself.
func TestProfileListJSON(t *testing.T) {
	app := testApp(t)
	seedRegistered(t, app, "default", "http://ctl:8000")

	// An API-key profile alongside, to cover the auth split + masking.
	p, err := profile.Open(app.Paths, "keyed")
	if err != nil {
		t.Fatalf("open profile: %v", err)
	}
	if err := p.SaveMeta(&profile.Meta{AuthMode: profile.AuthModeAPIKey, BaseURL: "http://ctl:8000"}); err != nil {
		t.Fatalf("save meta: %v", err)
	}
	if err := p.SaveAPIKey("jak_supersecret_value_123456"); err != nil {
		t.Fatalf("save api key: %v", err)
	}

	if err := app.profileListJSON(); err != nil {
		t.Fatalf("profileListJSON: %v", err)
	}
	raw := app.Out.(*bytes.Buffer).String()

	var doc struct {
		Profiles []struct {
			Name       string `json:"name"`
			Auth       string `json:"auth"`
			Registered bool   `json:"registered"`
			Token      string `json:"token"`
			APIKey     string `json:"api_key"`
		} `json:"profiles"`
		Active string `json:"active"`
	}
	if err := json.Unmarshal([]byte(raw), &doc); err != nil {
		t.Fatalf("output is not JSON: %v\n%s", err, raw)
	}
	if len(doc.Profiles) != 2 {
		t.Fatalf("want 2 profiles, got %d:\n%s", len(doc.Profiles), raw)
	}
	byName := map[string]int{}
	for i, r := range doc.Profiles {
		byName[r.Name] = i
	}
	reg := doc.Profiles[byName["default"]]
	if !reg.Registered || reg.Auth != profile.AuthModeDCR || reg.Token == "" {
		t.Errorf("registered dcr row wrong: %+v", reg)
	}
	keyed := doc.Profiles[byName["keyed"]]
	if keyed.Auth != profile.AuthModeAPIKey {
		t.Errorf("api-key row wrong: %+v", keyed)
	}
	if bytes.Contains([]byte(raw), []byte("jak_supersecret_value_123456")) {
		t.Errorf("raw API key leaked into JSON output:\n%s", raw)
	}
}

// TestProfileListJSONEmpty: a machine consumer gets a well-formed empty list on
// a fresh home, not prose.
func TestProfileListJSONEmpty(t *testing.T) {
	app := testApp(t)
	if err := app.profileListJSON(); err != nil {
		t.Fatalf("profileListJSON: %v", err)
	}
	var doc struct {
		Profiles []any `json:"profiles"`
	}
	raw := app.Out.(*bytes.Buffer).Bytes()
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("output is not JSON: %v\n%s", err, raw)
	}
	if len(doc.Profiles) != 0 {
		t.Errorf("want empty profiles, got %v", doc.Profiles)
	}
}
