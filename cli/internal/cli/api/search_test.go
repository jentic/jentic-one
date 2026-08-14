package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSearchCmdJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/search":
			_, _ = w.Write([]byte(`{
				"data": [
					{"type":"operation","api":{"vendor":"acme","name":"pets","version":"v1","host":"acme.com"},"operation_id":"op1","method":"GET","url":"/pets","name":"List Pets","relevance_score":0.9,"_links":{"inspect":"/inspect?id=GET%20/pets"}},
					{"type":"operation","api":{"vendor":"acme","name":"pets","version":"v1","host":"acme.com"},"operation_id":"op2","method":"POST","url":"/pets","name":"Create Pet","relevance_score":0.8,"_links":{"inspect":"/inspect?id=POST%20/pets"}}
				],
				"has_more": false,
				"next_cursor": ""
			}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	root := newAPIRootCmd(app.App)
	out := new(bytes.Buffer)
	app.Out = out
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"search", "pets", "--json"})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	var result map[string]any
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal output: %v\nraw: %s", err, out.String())
	}
	data, ok := result["data"].([]any)
	if !ok {
		t.Fatalf("data field not an array: %v", result)
	}
	if len(data) != 2 {
		t.Errorf("got %d results, want 2", len(data))
	}
	if result["has_more"] != false {
		t.Errorf("has_more = %v, want false", result["has_more"])
	}
}

func TestSearchCmdEmptyResultsEmitEmptyArray(t *testing.T) {
	// Regression: an empty result set must serialize as `"data": []`, never
	// `null`, so an agent's `jq '.data[]'` recipe never crashes (the CLI side of
	// issue #671).
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/search" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"data":[],"has_more":false,"next_cursor":null}`))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"search", "nothing", "--json"})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if strings.Contains(out.String(), `"data":null`) || strings.Contains(out.String(), `"data": null`) {
		t.Fatalf("data serialized as null, want []: %s", out.String())
	}
	var result map[string]any
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	data, ok := result["data"].([]any)
	if !ok {
		t.Fatalf("data is not an array (likely null): %v", result["data"])
	}
	if len(data) != 0 {
		t.Errorf("got %d results, want 0", len(data))
	}
}

func TestInspectHint(t *testing.T) {
	tests := []struct {
		name string
		hit  searchHit
		want string
	}{
		{
			name: "absolute url uses the resolvable METHOD-URL form",
			hit: searchHit{
				OperationID: "op_abc",
				Links:       searchLinks{Inspect: "/inspect?id=GET%20https://api.acme.com/v1/things"},
			},
			want: "GET https://api.acme.com/v1/things",
		},
		{
			name: "host-relative link falls back to the registry operation_id",
			hit: searchHit{
				OperationID: "op_abc",
				Links:       searchLinks{Inspect: "/inspect?id=GET%20/things"},
			},
			want: "op_abc",
		},
		{
			name: "missing link falls back to the registry operation_id",
			hit: searchHit{
				OperationID: "op_abc",
				Links:       searchLinks{Inspect: ""},
			},
			want: "op_abc",
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := inspectHint(tc.hit); got != tc.want {
				t.Errorf("inspectHint() = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestSearchCmdAutopagination(t *testing.T) {
	callCount := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/search" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		callCount++
		if callCount == 1 {
			_, _ = w.Write([]byte(`{
				"data": [{"type":"operation","api":{"vendor":"x","name":"x","version":"v1","host":"x"},"operation_id":"op1","method":"GET","url":"/a","relevance_score":1.0,"_links":{"inspect":"/inspect?id=GET%20/a"}}],
				"has_more": true,
				"next_cursor": "page2"
			}`))
		} else {
			_, _ = w.Write([]byte(`{
				"data": [{"type":"operation","api":{"vendor":"x","name":"x","version":"v1","host":"x"},"operation_id":"op2","method":"POST","url":"/b","relevance_score":0.5,"_links":{"inspect":"/inspect?id=POST%20/b"}}],
				"has_more": false,
				"next_cursor": ""
			}`))
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"search", "test", "--all", "--json"})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if callCount != 2 {
		t.Errorf("expected 2 search calls (autopagination), got %d", callCount)
	}

	var result map[string]any
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	data := result["data"].([]any)
	if len(data) != 2 {
		t.Errorf("got %d results after autopagination, want 2", len(data))
	}
}

func TestSearchCmd501FriendlyMessage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/search" {
			w.WriteHeader(http.StatusNotImplemented)
			_, _ = w.Write([]byte(`{"detail":"not supported"}`))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"search", "test", "--json"})

	err := root.Execute()
	if err == nil {
		t.Fatal("expected error for 501")
	}
	if !strings.Contains(err.Error(), "search is not enabled") {
		t.Errorf("error = %q, want search-disabled hint", err.Error())
	}
}
