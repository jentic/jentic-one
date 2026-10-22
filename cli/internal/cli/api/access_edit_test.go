package api

// access_edit_test.go covers asking for and amending an access change against
// an httptest control plane: the wire body (no agent_id, add/remove/set and
// scopes shaped as the route takes them), an already-open edit pointing at
// amend, and a check_access refusal pointing at show_access.

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

type editControlPlane struct {
	status int
	body   string
	seen   map[string]any
	path   string
}

func (c *editControlPlane) server(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c.path = r.URL.Path
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &c.seen)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(c.status)
		_, _ = w.Write([]byte(c.body))
	}))
}

const editCreatedBody = `{"session_id":"cs_e","approval_url":"https://one.example/app/agents?approve=cs_e",
	"poll_token":"pt_secret","revision_id":"csr_1","resolved_flow":"edit"}`

func TestMCPRequestAccessChange_ShapesTheWireAndWithholdsThePollToken(t *testing.T) {
	cp := &editControlPlane{status: http.StatusCreated, body: editCreatedBody}
	srv := cp.server(t)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestAccessChange(activeCtx(srv.URL), callToolRequest("request_access_change",
		`{"credential_id":"cred_1","rules":{"add":[{"effect":"allow","methods":["POST"],"path":"/v1/send",
		"match_mode":"exact"},{"rule":{"effect":"deny","path":"/v1/admin"},"before":"apr_1"}],
		"remove":["apr_9"]},"scopes":{"add":["gmail.send"],"remove":["gmail.modify"]},"resume":true,
		"reason":"send replies"}`))
	if err != nil {
		t.Fatalf("handleRequestAccessChange: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}
	if cp.path != "/integrations:edit-connection" {
		t.Errorf("path = %q", cp.path)
	}
	if _, has := cp.seen["agent_id"]; has {
		t.Errorf("agent_id rode the wire: the control plane injects the caller")
	}
	rules, _ := cp.seen["rules"].(map[string]any)
	adds, _ := rules["add"].([]any)
	if len(adds) != 2 || adds[1].(map[string]any)["before"] != "apr_1" {
		t.Errorf("rules.add = %v, want two adds, the second before apr_1", rules["add"])
	}
	if remove, _ := rules["remove"].([]any); len(remove) != 1 || remove[0] != "apr_9" {
		t.Errorf("rules.remove = %v", rules["remove"])
	}
	scopes, _ := cp.seen["scopes"].(map[string]any)
	if add, _ := scopes["add"].([]any); len(add) != 1 || add[0] != "gmail.send" {
		t.Errorf("scopes = %v", cp.seen["scopes"])
	}
	if remove, _ := scopes["remove"].([]any); len(remove) != 1 || remove[0] != "gmail.modify" {
		t.Errorf("scopes.remove = %v", cp.seen["scopes"])
	}
	if cp.seen["resume"] != true || cp.seen["reason"] != "send replies" {
		t.Errorf("resume/reason = %v / %v", cp.seen["resume"], cp.seen["reason"])
	}
	payload := decodeToolJSON(t, res)
	if payload["session_id"] != "cs_e" || payload["revision_id"] != "csr_1" {
		t.Errorf("payload = %v", payload)
	}
	if strings.Contains(toolResultText(res), "pt_secret") {
		t.Errorf("the poll token leaked into the tool result")
	}
}

func TestMCPRequestAccessChange_AnOpenEditPointsAtAmend(t *testing.T) {
	cp := &editControlPlane{status: http.StatusConflict, body: `{"type":"edit_open","status":409,
		"detail":"already open","session_id":"cs_open"}`}
	srv := cp.server(t)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestAccessChange(activeCtx(srv.URL), callToolRequest("request_access_change",
		`{"credential_id":"cred_1","resume":true}`))
	if err != nil {
		t.Fatalf("handleRequestAccessChange: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if res.IsError || payload["session_id"] != "cs_open" || payload["next_tool"] != "amend_request" {
		t.Errorf("payload = %v, want the open session and next_tool amend_request", payload)
	}
}

func TestMCPRequestAccessChange_ACheckAccessRefusalPointsAtShowAccess(t *testing.T) {
	cp := &editControlPlane{status: http.StatusUnprocessableEntity, body: `{"type":"unknown_rule","status":422,
		"detail":"rule 'apr_x' is not on this binding","agent_directive":{"strategy":"check_access",
		"parameters":{"credential_id":"cred_1","suggested_command":"jentic access show cred_1"},
		"human_readable_instruction":"Check your access."}}`}
	srv := cp.server(t)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestAccessChange(activeCtx(srv.URL), callToolRequest("request_access_change",
		`{"credential_id":"cred_1","rules":{"remove":["apr_x"]}}`))
	if err != nil {
		t.Fatalf("handleRequestAccessChange: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want a soft error")
	}
	payload := decodeToolJSON(t, res)
	if payload["next_tool"] != "show_access" || payload["error_code"] != ux.CodeResolveFailed {
		t.Errorf("payload = %v, want RESOLVE_FAILED and next_tool show_access", payload)
	}
	if directive, _ := payload["agent_directive"].(map[string]any); directive["strategy"] != "check_access" {
		t.Errorf("agent_directive = %v", payload["agent_directive"])
	}
}

func TestMCPRequestAccessChange_SetWithAddIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	_, err := s.handleRequestAccessChange(activeCtx("http://127.0.0.1:0"), callToolRequest("request_access_change",
		`{"credential_id":"cred_1","rules":{"set":[{"effect":"allow","path":"/"}],"add":[{"effect":"deny","path":"/x"}]}}`))
	if err == nil {
		t.Fatal("want invalid params for set combined with add")
	}
}

func TestMCPAmendRequest_SendsOnlyWhatChanges(t *testing.T) {
	cp := &editControlPlane{status: http.StatusOK, body: `{"session_id":"cs_e","revision_id":"csr_2","revision_number":2}`}
	srv := cp.server(t)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleAmendRequest(activeCtx(srv.URL), callToolRequest("amend_request",
		`{"session_id":"cs_e","requested_scopes":["repo"],"reason":"also labels"}`))
	if err != nil {
		t.Fatalf("handleAmendRequest: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}
	if cp.path != "/connect-sessions/cs_e:amend" {
		t.Errorf("path = %q", cp.path)
	}
	if _, has := cp.seen["rules"]; has {
		t.Errorf("rules rode the wire though none were given: %v", cp.seen)
	}
	if cp.seen["reason"] != "also labels" {
		t.Errorf("reason = %v", cp.seen["reason"])
	}
	if payload := decodeToolJSON(t, res); payload["revision_number"] != float64(2) {
		t.Errorf("payload = %v", payload)
	}
}

func TestAccessEdit_PrintsTheApprovalURL(t *testing.T) {
	withXDG(t)
	cp := &editControlPlane{status: http.StatusCreated, body: editCreatedBody}
	srv := cp.server(t)
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "access", "edit", "cred_1",
		"--add", `[{"effect":"allow","methods":["GET"],"path":"/v1/x","match_mode":"exact"}]`, "--before", "apr_1")
	if err != nil {
		t.Fatalf("access edit: %v\n%s", err, out)
	}
	if !strings.Contains(out, "https://one.example/app/agents?approve=cs_e") || strings.Contains(out, "pt_secret") {
		t.Errorf("output = %s", out)
	}
	rules, _ := cp.seen["rules"].(map[string]any)
	adds, _ := rules["add"].([]any)
	if len(adds) != 1 || adds[0].(map[string]any)["before"] != "apr_1" {
		t.Errorf("rules = %v", cp.seen["rules"])
	}
}

func TestAccessEditWait_AConflictedRequestPointsAtAmend(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/integrations:edit-connection" {
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(editCreatedBody))
			return
		}
		// The binding moved under the request in a way it can't follow.
		_, _ = w.Write([]byte(`{"status":"pending","error_code":"conflicted"}`))
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "access", "edit", "cred_1", "--resume", "--wait", "--timeout", "5s")
	if err == nil {
		t.Fatalf("want a coded error for a conflicted request, got output %s", out)
	}
	if !strings.Contains(err.Error(), "conflicts with a change to your access") {
		t.Errorf("err = %v, want the conflict named", err)
	}
	if coded := accessEditConflicted("cs_e", "cred_1"); !strings.Contains(coded.Actionable, "jentic access amend cs_e") {
		t.Errorf("actionable = %q, want the amend step", coded.Actionable)
	}
}

func TestAccessEdit_SetScopesIsNotCombinedWithAddOrRemove(t *testing.T) {
	withXDG(t)
	cp := &editControlPlane{status: http.StatusCreated, body: editCreatedBody}
	srv := cp.server(t)
	defer srv.Close()

	if _, err := runConnectTree(t, srv.URL, "access", "edit", "cred_1",
		"--set-scopes", "repo", "--remove-scopes", "gist"); err == nil {
		t.Fatal("want an argument error for --set-scopes with --remove-scopes")
	}
	if _, err := runConnectTree(t, srv.URL, "access", "edit", "cred_1", "--set-scopes", "repo,read:org"); err != nil {
		t.Fatalf("access edit --set-scopes: %v", err)
	}
	scopes, _ := cp.seen["scopes"].(map[string]any)
	if set, _ := scopes["set"].([]any); len(set) != 2 {
		t.Errorf("scopes = %v, want the full set", cp.seen["scopes"])
	}
}

func TestAccessEdit_ScopeCommentsSayWhy(t *testing.T) {
	withXDG(t)
	cp := &editControlPlane{status: http.StatusCreated, body: editCreatedBody}
	srv := cp.server(t)
	defer srv.Close()

	if _, err := runConnectTree(t, srv.URL, "access", "edit", "cred_1",
		"--add-scopes", "gmail.send", "--scope-comment", "gmail.send=to send drafted replies"); err != nil {
		t.Fatalf("access edit: %v", err)
	}
	scopes, _ := cp.seen["scopes"].(map[string]any)
	comments, _ := scopes["comments"].(map[string]any)
	if comments["gmail.send"] != "to send drafted replies" {
		t.Errorf("scopes = %v, want the reason by scope", cp.seen["scopes"])
	}
}
