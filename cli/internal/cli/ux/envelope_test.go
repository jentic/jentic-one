package ux

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestNewPage_AndHints(t *testing.T) {
	last := NewPage([]string{"a", "b"}, "")
	if last.HasNext() {
		t.Error("empty NextToken should mean no next page")
	}
	if last.NextHint() != "" {
		t.Errorf("last page should have no hint, got %q", last.NextHint())
	}

	more := NewPage([]int{1, 2, 3}, "cursor-xyz")
	if !more.HasNext() {
		t.Error("non-empty NextToken should mean HasNext")
	}
	if !strings.Contains(more.NextHint(), "--cursor cursor-xyz") {
		t.Errorf("hint should name the cursor flag+token, got %q", more.NextHint())
	}
}

func TestPage_RendersAsAgentEnvelope(t *testing.T) {
	// AgentUX.Render on a Page must emit the {schema_version, items, next_token}
	// envelope. We assert the marshaled shape (Render writes to os.Stdout).
	p := NewPage([]map[string]any{{"id": "1"}}, "next-1")
	out := safeMarshal(p)
	var got map[string]any
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("page envelope not valid JSON: %v", err)
	}
	if got["schema_version"] != "1" {
		t.Errorf("schema_version = %v", got["schema_version"])
	}
	if got["next_token"] != "next-1" {
		t.Errorf("next_token = %v", got["next_token"])
	}
	if _, ok := got["items"]; !ok {
		t.Error("items key missing from page envelope")
	}
}

func TestResult_StatusVerbsAreClosed(t *testing.T) {
	// The status constants are the closed vocabulary (13 §2); guard the values so a
	// rename is a visible break.
	want := map[string]string{
		StatusCreated: "created", StatusAdded: "added", StatusUpdated: "updated",
		StatusSwitched: "switched", StatusDeleted: "deleted",
		StatusRegistered: "registered", StatusPending: "pending",
	}
	for got, exp := range want {
		if got != exp {
			t.Errorf("status verb drift: %q != %q", got, exp)
		}
	}
}
