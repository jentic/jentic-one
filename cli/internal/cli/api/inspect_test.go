package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestInspectCmdJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/inspect" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if r.Header.Get("Accept") != "application/json" {
			t.Errorf("accept = %q, want application/json", r.Header.Get("Accept"))
		}
		if r.URL.Query().Get("operation_id") != "listPets" {
			t.Errorf("operation_id = %q", r.URL.Query().Get("operation_id"))
		}
		_, _ = w.Write([]byte(`{"method":"GET","path":"/pets","parameters":[{"name":"limit","in":"query"}]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"inspect", "listPets", "--json"})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	var result map[string]any
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v\nraw: %s", err, out.String())
	}
	if result["method"] != "GET" {
		t.Errorf("method = %v", result["method"])
	}
	if result["path"] != "/pets" {
		t.Errorf("path = %v", result["path"])
	}
}

func TestInspectCmdMarkdown(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/inspect" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if r.Header.Get("Accept") != "text/markdown" {
			t.Errorf("accept = %q, want text/markdown", r.Header.Get("Accept"))
		}
		_, _ = w.Write([]byte("# GET /pets\n\nList all pets"))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"inspect", "listPets", "--format", "markdown"})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(out.String(), "# GET /pets") {
		t.Errorf("output = %q, want markdown", out.String())
	}
}

func TestInspectCmdNotFoundExitsCode2(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"not found"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"inspect", "badOp", "--json"})

	err := root.Execute()
	if err == nil {
		t.Fatal("expected error")
	}
	var ec *exitCodeError
	if !errors.As(err, &ec) {
		t.Fatalf("error type = %T, want *exitCodeError", err)
	}
	if ec.Code != 2 {
		t.Errorf("exit code = %d, want 2", ec.Code)
	}
}

func TestInspectCmdRevisionParam(t *testing.T) {
	var gotRevision string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotRevision = r.URL.Query().Get("revision_id")
		_, _ = w.Write([]byte(`{"method":"GET","path":"/pets"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"inspect", "listPets", "--revision", "rev9", "--json"})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotRevision != "rev9" {
		t.Errorf("revision_id = %q, want rev9", gotRevision)
	}
}
