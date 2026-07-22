package cmd

import (
	"encoding/json"
	"testing"
)

func TestPlanBuildsFullProvisioningChain(t *testing.T) {
	opts := &accessRequestOptions{
		provision: "posthog.com/posthog-api",
		auth:      "bearer",
		rulesJSON: `[{"effect":"allow","methods":["GET"],"path":".*"}]`,
	}
	items, err := opts.plan()
	if err != nil {
		t.Fatalf("plan() error: %v", err)
	}
	if len(items) != 4 {
		t.Fatalf("expected 4 items, got %d", len(items))
	}

	want := []struct{ rt, action string }{
		{"toolkit", "create"},
		{"credential", "provision"},
		{"credential", "bind"},
		{"toolkit", "bind"},
	}
	for i, w := range want {
		if items[i].ResourceType != w.rt || items[i].Action != w.action {
			t.Errorf("item[%d] = %s:%s, want %s:%s", i, items[i].ResourceType, items[i].Action, w.rt, w.action)
		}
	}

	// The credential:bind item carries the proposed rules.
	if len(items[2].Rules) != 1 || items[2].Rules[0].Effect != "allow" {
		t.Errorf("credential:bind should carry the proposed allow rule, got %+v", items[2].Rules)
	}

	// The provision item carries the detected auth type and the API reference.
	if items[1].ResourceReference["security_scheme"] != "bearer" {
		t.Errorf("credential:provision should carry security_scheme=bearer, got %v", items[1].ResourceReference)
	}
	if items[1].ResourceReference["vendor"] != "posthog.com" {
		t.Errorf("credential:provision should carry the api vendor, got %v", items[1].ResourceReference)
	}
}

func TestPlanNoAuthOmitsCredentialProvision(t *testing.T) {
	opts := &accessRequestOptions{provision: "open-meteo.com/forecast", auth: "none"}
	items, err := opts.plan()
	if err != nil {
		t.Fatalf("plan() error: %v", err)
	}
	// No credential:provision item for a no-auth API.
	for _, it := range items {
		if it.ResourceType == "credential" && it.Action == "provision" {
			t.Fatalf("no-auth plan should not include credential:provision; items=%+v", items)
		}
	}
	// Still has toolkit:create, credential:bind, toolkit:bind.
	if len(items) != 3 {
		t.Fatalf("expected 3 items for no-auth plan, got %d", len(items))
	}
}

func TestPlanRejectsInvalidAuth(t *testing.T) {
	opts := &accessRequestOptions{provision: "x.com/api", auth: "kerberos"}
	if _, err := opts.plan(); err == nil {
		t.Fatal("expected error for invalid --auth value")
	}
}

func TestPlanRejectsBadRulesJSON(t *testing.T) {
	opts := &accessRequestOptions{provision: "x.com/api", rulesJSON: "not json"}
	if _, err := opts.plan(); err == nil {
		t.Fatal("expected error for malformed --rules-json")
	}
}

func TestParseProposedRulesEmpty(t *testing.T) {
	rules, err := parseProposedRules("")
	if err != nil {
		t.Fatalf("empty rules should not error: %v", err)
	}
	if rules != nil {
		t.Fatalf("empty rules should yield nil, got %+v", rules)
	}
}

func TestPlanItemsSerializeWithoutEmptyIDs(t *testing.T) {
	opts := &accessRequestOptions{provision: "x.com/api", auth: "bearer"}
	items, _ := opts.plan()
	// credential:bind has no resource_id/to_id yet (filled at approval); ensure
	// they are omitted from the wire form rather than sent as empty strings.
	b, _ := json.Marshal(items[2])
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	if _, ok := m["resource_id"]; ok {
		t.Errorf("credential:bind should omit empty resource_id, got %s", b)
	}
	if _, ok := m["to_id"]; ok {
		t.Errorf("credential:bind should omit empty to_id, got %s", b)
	}
}
