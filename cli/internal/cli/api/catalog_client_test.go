package api

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/client/generated/control"
)

// TestToCatalogEntryMatchPassthrough pins the optional `match` evidence a
// server-side ranking strategy may attach: absent on the wire → absent in the
// JSON the CLI and MCP emit (omitempty keeps browse/default-ranking entries'
// exact shape); present → channels passed through verbatim, unknown values
// included, as a copy the generated response can't alias.
func TestToCatalogEntryMatchPassthrough(t *testing.T) {
	base := control.CatalogEntryResponse{ApiId: "stripe.com"}

	without, err := json.Marshal(toCatalogEntry(base))
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if strings.Contains(string(without), `"match"`) {
		t.Errorf("entry without match evidence = %s, want no match key", without)
	}

	channels := []string{"alias", "some_future_channel"}
	withMatch := base
	withMatch.Match = &control.CatalogMatchResponse{Channels: channels}
	entry := toCatalogEntry(withMatch)
	channels[0] = "mutated"
	got, err := json.Marshal(entry)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if !strings.Contains(string(got), `"match":{"channels":["alias","some_future_channel"]}`) {
		t.Errorf("entry with match = %s, want the channels passed through verbatim", got)
	}
}
