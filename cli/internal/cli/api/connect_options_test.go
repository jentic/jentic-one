package api

// connect_options_test.go covers reading how an API would be connected: the
// route the tool and command call, the next step they add, and an unknown
// API pointing at import_api.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const connectOptionsBody = `{"vendor":"googleapis.com","name":"gmail","version":"v1","display_name":"Gmail",
	"api_target_supported":true,"schemes":[{"name":"oauth","type":"oauth2","kind":"oauth2",
	"flow":"authorization_code","vendor_key":"gmail","registration_id":null,
	"scopes":[{"name":"gmail.send","description":"Send","classification":"write","default":false,"requestable":true}]}]}`

func connectOptionsPlane(t *testing.T, status int, body string, seen *string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*seen = r.URL.Path + "?" + r.URL.RawQuery
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
}

func TestMCPInspectAPI_ReadsTheConnectOptions(t *testing.T) {
	var seen string
	srv := connectOptionsPlane(t, http.StatusOK, connectOptionsBody, &seen)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleInspectAPI(activeCtx(srv.URL), callToolRequest("inspect_api",
		`{"api":"googleapis.com/gmail/v1","registration_id":"oar_1"}`))
	if err != nil {
		t.Fatalf("handleInspectAPI: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}
	if seen != "/integrations/googleapis.com/gmail/v1/connect-options?registration_id=oar_1" {
		t.Errorf("request = %q", seen)
	}
	payload := decodeToolJSON(t, res)
	schemes, _ := payload["schemes"].([]any)
	if len(schemes) != 1 || !strings.Contains(payload["next_step"].(string), "request_connection") {
		t.Errorf("payload = %v", payload)
	}
}

func TestMCPInspectAPI_AnUnknownAPIPointsAtImport(t *testing.T) {
	var seen string
	srv := connectOptionsPlane(t, http.StatusNotFound,
		`{"type":"unknown_api","status":404,"detail":"no such API"}`, &seen)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleInspectAPI(activeCtx(srv.URL), callToolRequest("inspect_api", `{"api":"nope/nope/v0"}`))
	if err != nil {
		t.Fatalf("handleInspectAPI: %v", err)
	}
	if !res.IsError || decodeToolJSON(t, res)["next_tool"] != "import_api" {
		t.Errorf("result = %s, want a soft error pointing at import_api", toolResultText(res))
	}
}

func TestApisAuth_PrintsTheConnectOptions(t *testing.T) {
	withXDG(t)
	var seen string
	srv := connectOptionsPlane(t, http.StatusOK, connectOptionsBody, &seen)
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "apis", "auth", "googleapis.com/gmail/v1")
	if err != nil {
		t.Fatalf("apis auth: %v\n%s", err, out)
	}
	if !strings.Contains(out, "gmail.send") || !strings.Contains(out, "requestable") {
		t.Errorf("output = %s", out)
	}
}
