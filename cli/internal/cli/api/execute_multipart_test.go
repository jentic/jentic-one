package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// writeTemp writes content to dir/name and returns the path.
func writeTemp(t *testing.T, dir, name, content string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(content), 0o600); err != nil {
		t.Fatalf("write %s: %v", p, err)
	}
	return p
}

// wantUsageError asserts err is a MISSING_ARGUMENT CodedError whose message
// contains msgPart.
func wantUsageError(t *testing.T, err error, msgPart string) *ux.CodedError {
	t.Helper()
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("err = %v (%T), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeMissingArgument {
		t.Errorf("code = %q, want %q", coded.Code, ux.CodeMissingArgument)
	}
	if !strings.Contains(coded.Msg, msgPart) {
		t.Errorf("msg = %q, want it to contain %q", coded.Msg, msgPart)
	}
	if coded.Actionable == "" {
		t.Error("actionable must name the fix")
	}
	return coded
}

// TestParseFormFileSpec pins the --form-file grammar (#1320):
// key=@path[;type=…][;filename=…], suffixes peeled from the right so any other
// ';' stays in the path, a quoted path for one that itself ends in an
// option-looking segment, and a coded usage error for every malformed shape.
func TestParseFormFileSpec(t *testing.T) {
	ok := []struct {
		in   string
		want formFileSpec
	}{
		{"images=@face.jpg", formFileSpec{key: "images", path: "face.jpg"}},
		{"doc=@scan;type=application/pdf", formFileSpec{key: "doc", path: "scan", contentType: "application/pdf"}},
		{"doc=@scan;filename=r.pdf", formFileSpec{key: "doc", path: "scan", filename: "r.pdf"}},
		{"doc=@scan;type=application/pdf;filename=r.pdf", formFileSpec{key: "doc", path: "scan", contentType: "application/pdf", filename: "r.pdf"}},
		{"doc=@scan;filename=r.pdf;type=application/pdf", formFileSpec{key: "doc", path: "scan", contentType: "application/pdf", filename: "r.pdf"}},
		// Option names are case-insensitive; the media type is normalized.
		{"doc=@scan;TYPE=Image/PNG", formFileSpec{key: "doc", path: "scan", contentType: "image/png"}},
		// A ';' that is not a trailing option stays part of the path.
		{"doc=@a;b.txt", formFileSpec{key: "doc", path: "a;b.txt"}},
		{"doc=@a;b.txt;type=text/plain", formFileSpec{key: "doc", path: "a;b.txt", contentType: "text/plain"}},
		{"doc=@dir;x/file", formFileSpec{key: "doc", path: "dir;x/file"}},
		// Quoting keeps an option-looking tail inside the path.
		{`doc=@"odd;type=x/y"`, formFileSpec{key: "doc", path: "odd;type=x/y"}},
		{`doc=@"odd;filename=z";filename=clean.bin`, formFileSpec{key: "doc", path: "odd;filename=z", filename: "clean.bin"}},
		// A CR/LF in the PATH is fine when ;filename= supplies a clean header value.
		{"doc=@/tmp/a\nb;filename=clean", formFileSpec{key: "doc", path: "/tmp/a\nb", filename: "clean"}},
		// Value edges: '=' inside the path is preserved (only the first '=' splits).
		{"doc=@a=b.txt", formFileSpec{key: "doc", path: "a=b.txt"}},
		// A leading '"' is quoting only when a closing '"' is followed by the
		// end or ';'; otherwise it is a literal part of the path.
		{`k=@"weird`, formFileSpec{key: "k", path: `"weird`}},
		{`k=@"face.jpg"x`, formFileSpec{key: "k", path: `"face.jpg"x`}},
		{`k=@"a";type=text/plain`, formFileSpec{key: "k", path: "a", contentType: "text/plain"}},
		{`k=@"a"b";type=text/plain`, formFileSpec{key: "k", path: `a"b`, contentType: "text/plain"}},
	}
	for _, tc := range ok {
		t.Run("ok/"+tc.in, func(t *testing.T) {
			got, err := parseFormFileSpec(tc.in)
			if err != nil {
				t.Fatalf("parseFormFileSpec(%q): %v", tc.in, err)
			}
			if got != tc.want {
				t.Errorf("parseFormFileSpec(%q) = %+v, want %+v", tc.in, got, tc.want)
			}
		})
	}

	bad := []struct {
		in, msg string
	}{
		{"images=face.jpg", "expected key=@path"},
		{"=@face.jpg", "expected key=@path"},
		{" =@face.jpg", "expected key=@path"},
		{"images", "expected key=@path"},
		{"images=@", "expected key=@path"},
		{"images=@;type=text/plain", "expected key=@path"},
		{"images=@-", "stdin"},
		{"images=@-;type=text/plain", "stdin"},
		{`images=@"face.jpg";bogus=1`, "unknown option"},
		{`images=@"face.jpg";`, "unknown option"},
		{"images=@face.jpg;type=", "empty type="},
		{"images=@face.jpg;filename=", "empty filename="},
		{"images=@face.jpg;type=notamediatype", "invalid type="},
		{"images=@face.jpg;type=a/b;type=c/d", "more than once"},
		{"images=@face.jpg;filename=a;filename=b", "more than once"},
		// CR/LF header-injection attempts (field name, filename override,
		// type override, and the path's base name).
		{"ima\r\nX-Injected: 1\r\n\r\nges=@face.jpg", "CR or LF"},
		{"images\n=@face.jpg", "CR or LF"},
		{"images=@face.jpg;filename=a\r\nX-Injected: 1", "CR or LF"},
		{"images=@face.jpg;filename=a\nb", "CR or LF"},
		{"images=@face.jpg;type=text/plain\r\nX-Injected: 1", "invalid type="},
		{"images=@/tmp/evil\r\nX-Injected: 1", "CR or LF"},
	}
	for _, tc := range bad {
		t.Run("bad/"+tc.in, func(t *testing.T) {
			_, err := parseFormFileSpec(tc.in)
			wantUsageError(t, err, tc.msg)
		})
	}
}

// TestParseFormField pins the --form grammar: value edges (empty value, '='
// in the value), the @ footgun and its @@ escape, CR/LF-free names, and that a
// CR/LF in a field VALUE is body data and allowed.
func TestParseFormField(t *testing.T) {
	ok := []struct {
		in       string
		key, val string
	}{
		{"k=", "k", ""},
		{"a=b=c", "a", "b=c"},
		{"handle=@@lit", "handle", "@lit"},
		{"note=line1\r\nline2", "note", "line1\r\nline2"},
	}
	for _, tc := range ok {
		got, err := parseFormField(tc.in)
		if err != nil {
			t.Fatalf("parseFormField(%q): %v", tc.in, err)
		}
		if got.key != tc.key || got.value != tc.val {
			t.Errorf("parseFormField(%q) = %+v, want key=%q value=%q", tc.in, got, tc.key, tc.val)
		}
	}
	bad := []struct{ in, msg string }{
		{"novalue", "expected key=value"},
		{"=v", "expected key=value"},
		{"img=@face.jpg", "starts with @"},
		{"a\r\nContent-Type: text/html\r\n\r\nx=1", "CR or LF"},
		{"a\nb=1", "CR or LF"},
		{"a\rb=1", "CR or LF"},
	}
	for _, tc := range bad {
		_, err := parseFormField(tc.in)
		wantUsageError(t, err, tc.msg)
	}

	t.Run("@ refusal never echoes the value", func(t *testing.T) {
		_, err := parseFormField("token=@SECRET-VALUE")
		coded := wantUsageError(t, err, "starts with @")
		if strings.Contains(coded.Msg, "SECRET-VALUE") || strings.Contains(coded.Actionable, "SECRET-VALUE") {
			t.Errorf("error echoes the field value: msg=%q actionable=%q", coded.Msg, coded.Actionable)
		}
	})
}

// TestBuildMultipartBodyCRLFNeverReachesWire is the end-to-end security pin:
// a CR/LF-bearing name or filename yields no body at all, so no injected part
// header can be emitted.
func TestBuildMultipartBodyCRLFNeverReachesWire(t *testing.T) {
	f := writeTemp(t, t.TempDir(), "face.jpg", "JPEG")
	cases := []struct {
		name           string
		form, formFile []string
	}{
		{"field name", []string{"x\r\nX-Injected: 1=v"}, nil},
		{"file key", nil, []string{"x\r\nX-Injected: 1=@" + f}},
		{"filename override", nil, []string{"img=@" + f + ";filename=a\r\nX-Injected: 1"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			mp, err := buildMultipartBody(tc.form, tc.formFile, multipartBodyLimit)
			if mp != nil {
				t.Fatalf("a CR/LF name must never produce a body; got %q", mp.Body.String())
			}
			wantUsageError(t, err, "CR or LF")
		})
	}
}

// TestBuildMultipartBodySizeGuard pins the pre-flight size check (#1320): the
// summed payload (file stat sizes + field values) is checked against the limit
// BEFORE any file is read, and the assembled body (framing included — the
// broker caps the whole body) is checked exactly afterwards.
func TestBuildMultipartBodySizeGuard(t *testing.T) {
	dir := t.TempDir()
	const limit = 1024

	// A sparse file reports its full stat size without occupying disk; the
	// pre-flight must refuse it from the stat alone.
	sparse := filepath.Join(dir, "huge.bin")
	if err := os.WriteFile(sparse, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Truncate(sparse, 4*limit); err != nil {
		t.Fatal(err)
	}
	half := writeTemp(t, dir, "half.bin", strings.Repeat("h", 600))
	small := writeTemp(t, dir, "small.bin", strings.Repeat("s", 100))
	nearly := writeTemp(t, dir, "nearly.bin", strings.Repeat("n", limit-30))

	// The pre-flight sum counts values, file sizes, and the field names and
	// upload filenames that land in part headers.
	cases := []struct {
		name           string
		form, formFile []string
		wantTotal      int64 // exact Details.total_bytes from the pre-flight sum; 0 = framing case
	}{
		{"single oversize file", nil, []string{"f=@" + sparse}, 1 + int64(len("huge.bin")) + 4*limit},
		{"files sum over limit", nil, []string{"a=@" + half, "b=@" + half}, 2 * (1 + int64(len("half.bin")) + 600)},
		{"fields count toward the limit", []string{"big=" + strings.Repeat("v", 2000)}, nil, 3 + 2000},
		{"field plus file over limit", []string{"big=" + strings.Repeat("v", 1000)}, []string{"f=@" + small}, 3 + 1000 + 1 + int64(len("small.bin")) + 100},
		{"field names count toward the limit", []string{strings.Repeat("k", 1100) + "=v"}, nil, 1100 + 1},
		// Payload fits, framing tips it over: the exact post-build check.
		{"framing tips over", nil, []string{"f=@" + nearly}, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			mp, err := buildMultipartBody(tc.form, tc.formFile, limit)
			if mp != nil {
				t.Fatal("an over-limit body must not be returned")
			}
			coded := wantUsageError(t, err, "multipart/form-data limit")
			if got := coded.Details["reason"]; got != "body_too_large" {
				t.Errorf("reason = %v, want body_too_large", got)
			}
			if got := coded.Details["limit_bytes"]; got != int64(limit) {
				t.Errorf("limit_bytes = %v, want %d", got, limit)
			}
			total, _ := coded.Details["total_bytes"].(int64)
			if tc.wantTotal != 0 && total != tc.wantTotal {
				t.Errorf("total_bytes = %d, want the pre-flight sum %d (refused before reading)", total, tc.wantTotal)
			}
			if total <= limit {
				t.Errorf("total_bytes = %d, want > limit %d", total, limit)
			}
		})
	}

	t.Run("closing boundary tips over", func(t *testing.T) {
		// Size the file so the part fits the copy budget but the closing
		// boundary pushes the body exactly one byte over: only the exact
		// post-build check can catch it. The boundary length is fixed, so the
		// framing overhead measured on a probe is the same for the real build.
		probe := writeTemp(t, t.TempDir(), "edge.bin", strings.Repeat("p", 100))
		mp, err := buildMultipartBody(nil, []string{"f=@" + probe}, multipartBodyLimit)
		if err != nil {
			t.Fatal(err)
		}
		overhead := int64(mp.Body.Len()) - 100
		edge := writeTemp(t, t.TempDir(), "edge.bin", strings.Repeat("e", int(limit+1-overhead)))
		mp, err = buildMultipartBody(nil, []string{"f=@" + edge}, limit)
		if mp != nil {
			t.Fatal("a body one byte over the limit must not be returned")
		}
		coded := wantUsageError(t, err, "multipart/form-data limit")
		if total, _ := coded.Details["total_bytes"].(int64); total != limit+1 {
			t.Errorf("total_bytes = %d, want exactly limit+1 = %d", total, limit+1)
		}
	})

	t.Run("under limit passes", func(t *testing.T) {
		mp, err := buildMultipartBody([]string{"a=1"}, []string{"f=@" + small}, limit)
		if err != nil {
			t.Fatalf("buildMultipartBody: %v", err)
		}
		if mp.Summary.TotalBytes != int64(mp.Body.Len()) || mp.Summary.TotalBytes > limit {
			t.Errorf("total_bytes = %d, body = %d, limit = %d", mp.Summary.TotalBytes, mp.Body.Len(), limit)
		}
	})
}

// TestBuildMultipartBodyRefusesNonRegularFiles pins the regular-file-only
// contract: a device (/dev/zero) or an stdin alias (/dev/stdin, /dev/fd/0)
// backed by a pipe or terminal is refused at the pre-flight stat with a coded
// error, before any open or read — such a stdin may never send EOF (#1354). An
// alias that resolves to a regular file (stdin redirected from a file) is
// accepted, so those cases skip. The
// FIFO case is in execute_multipart_unix_test.go.
func TestBuildMultipartBodyRefusesNonRegularFiles(t *testing.T) {
	for _, p := range []string{"/dev/zero", "/dev/stdin", "/dev/fd/0"} {
		t.Run(p, func(t *testing.T) {
			fi, err := os.Stat(p)
			if err != nil {
				t.Skipf("no %s on this platform", p)
			}
			if fi.Mode().IsRegular() {
				t.Skipf("%s resolves to a regular file in this test process", p)
			}
			var mp *multipartBody
			runWithTimeout(t, 5*time.Second, "buildMultipartBody with "+p, func() {
				mp, err = buildMultipartBody(nil, []string{"f=@" + p}, 4096)
			})
			if mp != nil {
				t.Fatal("a non-regular file must not produce a body")
			}
			wantUsageError(t, err, "not a regular file")
		})
	}
}

// TestBuildMultipartBodyPartsAndSummary pins the assembled parts and the
// dry-run summary: overrides land in the part headers, repeated keys produce
// several parts (array-of-files), MIME falls back through path extension →
// filename extension → octet-stream, and the summary carries metadata only.
func TestBuildMultipartBodyPartsAndSummary(t *testing.T) {
	dir := t.TempDir()
	a := writeTemp(t, dir, "a.jpg", "AAAA")
	b := writeTemp(t, dir, "b.png", "BBBBBB")
	blob := writeTemp(t, dir, "blob", "PDFBYTES")
	noext := writeTemp(t, dir, "noext", "XY")

	mp, err := buildMultipartBody(
		[]string{"caption=SECRET-FIELD-VALUE", "empty="},
		[]string{
			"images=@" + a,
			"images=@" + b,
			"doc=@" + blob + ";type=application/pdf;filename=report.pdf",
			"raw=@" + noext + ";filename=pic.gif", // MIME from the filename override
			"bin=@" + noext,                       // nothing to infer from
		},
		multipartBodyLimit,
	)
	if err != nil {
		t.Fatalf("buildMultipartBody: %v", err)
	}

	type part struct{ name, filename, ct, body string }
	_, params, err := mime.ParseMediaType(mp.ContentType)
	if err != nil {
		t.Fatalf("content type %q: %v", mp.ContentType, err)
	}
	var got []part
	mr := multipart.NewReader(bytes.NewReader(mp.Body.Bytes()), params["boundary"])
	for {
		p, perr := mr.NextPart()
		if perr == io.EOF {
			break
		}
		if perr != nil {
			t.Fatalf("next part: %v", perr)
		}
		data, _ := io.ReadAll(p)
		got = append(got, part{p.FormName(), p.FileName(), p.Header.Get("Content-Type"), string(data)})
	}
	want := []part{
		{"caption", "", "", "SECRET-FIELD-VALUE"},
		{"empty", "", "", ""},
		{"images", "a.jpg", "image/jpeg", "AAAA"},
		{"images", "b.png", "image/png", "BBBBBB"},
		{"doc", "report.pdf", "application/pdf", "PDFBYTES"},
		{"raw", "pic.gif", "image/gif", "XY"},
		{"bin", "noext", "application/octet-stream", "XY"},
	}
	if len(got) != len(want) {
		t.Fatalf("parts = %+v, want %+v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("part %d = %+v, want %+v", i, got[i], want[i])
		}
	}

	wantSummary := []multipartPartSummary{
		{Name: "caption", Kind: "field", Bytes: int64(len("SECRET-FIELD-VALUE"))},
		{Name: "empty", Kind: "field", Bytes: 0},
		{Name: "images", Kind: "file", Filename: "a.jpg", ContentType: "image/jpeg", Bytes: 4},
		{Name: "images", Kind: "file", Filename: "b.png", ContentType: "image/png", Bytes: 6},
		{Name: "doc", Kind: "file", Filename: "report.pdf", ContentType: "application/pdf", Bytes: 8},
		{Name: "raw", Kind: "file", Filename: "pic.gif", ContentType: "image/gif", Bytes: 2},
		{Name: "bin", Kind: "file", Filename: "noext", ContentType: "application/octet-stream", Bytes: 2},
	}
	if mp.Summary.Kind != "multipart/form-data" || mp.Summary.TotalBytes != int64(mp.Body.Len()) {
		t.Errorf("summary kind/total = %q/%d, want multipart/form-data/%d", mp.Summary.Kind, mp.Summary.TotalBytes, mp.Body.Len())
	}
	if len(mp.Summary.Parts) != len(wantSummary) {
		t.Fatalf("summary parts = %d, want %d", len(mp.Summary.Parts), len(wantSummary))
	}
	for i, w := range wantSummary {
		if g := mp.Summary.Parts[i]; g != w {
			t.Errorf("summary part %d = %+v, want %+v", i, g, w)
		}
	}
	raw, _ := json.Marshal(mp.Summary)
	for _, secret := range []string{"SECRET-FIELD-VALUE", "AAAA", "PDFBYTES"} {
		if strings.Contains(string(raw), secret) {
			t.Errorf("summary leaks %q: %s", secret, raw)
		}
	}
}

// runMultipartExecute runs `jentic execute uploadPic <args>` against a fake
// control plane + broker, capturing the Render stream. brokerHit reports
// whether the broker leg was dialed; onBroker (optional) inspects that request.
func runMultipartExecute(t *testing.T, onBroker func(*http.Request), args ...string) (stdout string, brokerHit bool, err error) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"POST","url":"https://upstream.example/upload"}`))
			return
		}
		brokerHit = true
		if onBroker != nil {
			onBroker(r)
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(append(append([]string{"execute", "uploadPic"}, args...),
		"--broker-scheme", "http", "--broker-host", srv.Listener.Addr().String()))
	stdout = captureStdout(t, func() { err = root.Execute() })
	return stdout, brokerHit, err
}

// TestExecuteDryRunMultipartSummary pins the --dry-run parts summary (#1320)
// in both output shapes (agent JSON and explicit human, which pretty-prints
// the same plan): names, kinds, filenames, content types, per-part byte
// lengths and the total — and NEVER a field value or file content. A field's
// length is reported: framing is deterministic, so total_bytes would reveal it
// anyway. The plan headers carry the generated boundary Content-Type, and the
// broker is never dialed.
func TestExecuteDryRunMultipartSummary(t *testing.T) {
	dir := t.TempDir()
	img := writeTemp(t, dir, "face.jpg", "FILE-CONTENT-SECRET")
	blob := writeTemp(t, dir, "blob", "OTHER-FILE-CONTENT")

	for _, mode := range []string{"agent", "human"} {
		t.Run(mode, func(t *testing.T) {
			out, hit, err := runMultipartExecute(t, nil,
				"--mode", mode, "--dry-run",
				"--form", "api_token=FIELD-VALUE-SECRET",
				"--form-file", "images=@"+img,
				"--form-file", "doc=@"+blob+";type=application/pdf;filename=report.pdf",
			)
			if err != nil {
				t.Fatalf("execute --dry-run: %v", err)
			}
			if hit {
				t.Fatal("dry-run must not reach the broker")
			}
			for _, secret := range []string{"FIELD-VALUE-SECRET", "FILE-CONTENT-SECRET", "OTHER-FILE-CONTENT"} {
				if strings.Contains(out, secret) {
					t.Errorf("dry-run output leaks %q:\n%s", secret, out)
				}
			}

			var plan struct {
				Operation string `json:"operation"`
				DryRun    bool   `json:"dry_run"`
				Payload   struct {
					Headers map[string]string `json:"headers"`
					Body    struct {
						Kind  string `json:"kind"`
						Parts []struct {
							Name        string `json:"name"`
							Kind        string `json:"kind"`
							Filename    string `json:"filename"`
							ContentType string `json:"content_type"`
							Bytes       *int64 `json:"bytes"`
						} `json:"parts"`
						TotalBytes int64 `json:"total_bytes"`
					} `json:"body"`
				} `json:"payload"`
			}
			if err := json.Unmarshal([]byte(out), &plan); err != nil {
				t.Fatalf("plan is not JSON (%v):\n%s", err, out)
			}
			if plan.Operation != "brokerExecute" || !plan.DryRun {
				t.Errorf("operation/dry_run = %q/%v", plan.Operation, plan.DryRun)
			}
			ct := plan.Payload.Headers["Content-Type"]
			if mt, params, perr := mime.ParseMediaType(ct); perr != nil || mt != "multipart/form-data" || params["boundary"] == "" {
				t.Errorf("plan Content-Type = %q, want multipart/form-data with a boundary", ct)
			}
			body := plan.Payload.Body
			if body.Kind != "multipart/form-data" || body.TotalBytes <= 0 || len(body.Parts) != 3 {
				t.Fatalf("body summary = %+v", body)
			}
			field, file1, file2 := body.Parts[0], body.Parts[1], body.Parts[2]
			if field.Name != "api_token" || field.Kind != "field" || field.Filename != "" ||
				field.Bytes == nil || *field.Bytes != int64(len("FIELD-VALUE-SECRET")) {
				t.Errorf("field summary = %+v (a field carries its value length, no filename)", field)
			}
			if file1.Name != "images" || file1.Kind != "file" || file1.Filename != "face.jpg" ||
				file1.ContentType != "image/jpeg" || file1.Bytes == nil || *file1.Bytes != int64(len("FILE-CONTENT-SECRET")) {
				t.Errorf("file summary = %+v", file1)
			}
			if file2.Filename != "report.pdf" || file2.ContentType != "application/pdf" {
				t.Errorf("override summary = %+v", file2)
			}
		})
	}
}

// TestExecuteMultipartOverridesOnTheWire pins that ;type=/;filename= reach
// the upstream part headers and that repeated --form-file keys arrive as
// several parts under one name (the array-of-files pattern).
func TestExecuteMultipartOverridesOnTheWire(t *testing.T) {
	dir := t.TempDir()
	blob := writeTemp(t, dir, "blob", "PNGBYTES")
	a := writeTemp(t, dir, "a.txt", "one")
	b := writeTemp(t, dir, "b.txt", "two")

	type part struct{ name, filename, ct, body string }
	var got []part
	_, hit, err := runMultipartExecute(t, func(r *http.Request) {
		_, params, perr := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if perr != nil {
			t.Errorf("content type: %v", perr)
			return
		}
		mr := multipart.NewReader(r.Body, params["boundary"])
		for {
			p, nerr := mr.NextPart()
			if nerr != nil {
				return
			}
			data, _ := io.ReadAll(p)
			got = append(got, part{p.FormName(), p.FileName(), p.Header.Get("Content-Type"), string(data)})
		}
	},
		"--json",
		"--form-file", "pic=@"+blob+";type=image/png;filename=pic.png",
		"--form-file", "files=@"+a,
		"--form-file", "files=@"+b,
	)
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !hit {
		t.Fatal("broker not called")
	}
	want := []part{
		{"pic", "pic.png", "image/png", "PNGBYTES"},
		{"files", "a.txt", "text/plain; charset=utf-8", "one"},
		{"files", "b.txt", "text/plain; charset=utf-8", "two"},
	}
	if len(got) != len(want) {
		t.Fatalf("parts = %+v, want %+v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("part %d = %+v, want %+v", i, got[i], want[i])
		}
	}
}

// TestExecuteMultipartRefusedBeforeBroker pins the command-level refusals: an
// upload over the broker's real multipart cap (a sparse file, so the test
// allocates nothing), stdin (@-), CR/LF names, and --form-file + --data-file
// all fail with a coded usage error and never dial the broker.
func TestExecuteMultipartRefusedBeforeBroker(t *testing.T) {
	dir := t.TempDir()
	huge := filepath.Join(dir, "huge.bin")
	if err := os.WriteFile(huge, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Truncate(huge, multipartBodyLimit+1); err != nil {
		t.Fatal(err)
	}
	small := writeTemp(t, dir, "small.txt", "x")
	data := writeTemp(t, dir, "body.json", `{}`)

	cases := []struct {
		name   string
		args   []string
		msg    string
		reason string // expected details.reason; "" = not asserted
	}{
		{"over the broker cap", []string{"--form-file", "f=@" + huge}, "52428800-byte (50 MiB)", "body_too_large"},
		{"stdin file part", []string{"--form-file", "f=@-"}, "stdin", ""},
		{"crlf form name", []string{"--form", "a\r\nX-Injected: 1=v"}, "CR or LF", ""},
		{"crlf filename", []string{"--form-file", "f=@" + small + ";filename=x\r\nX-Injected: 1"}, "CR or LF", ""},
		{"form-file with data-file", []string{"--form-file", "f=@" + small, "--data-file", data}, "cannot be combined", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, hit, err := runMultipartExecute(t, nil, tc.args...)
			if hit {
				t.Error("broker must not be dialed")
			}
			coded := wantUsageError(t, err, tc.msg)
			if tc.reason != "" && coded.Details["reason"] != tc.reason {
				t.Errorf("details.reason = %v, want %q", coded.Details["reason"], tc.reason)
			}
		})
	}
}
