package api

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/textproto"
	"os"
	"path/filepath"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// multipartBodyLimit is the broker's default request-body cap for
// multipart/form-data: the "multipart/form-data" entry of
// UpstreamClientConfig.max_request_bytes_by_type in
// src/jentic_one/shared/config/egress.py, enforced mid-stream (413) by
// _read_capped_body in src/jentic_one/broker/web/routers/execute.py. The cap is
// on the whole request body, multipart framing included. execute checks the
// assembled body against it before sending so an oversize upload fails fast and
// locally instead of being buffered in RAM and refused by the broker. The CLI
// has no knowledge of an operator-raised broker cap: a deployment that raises
// max_request_bytes_by_type["multipart/form-data"] above 50 MiB still cannot
// receive a larger multipart upload from this CLI (and nothing above the SDK
// transport's client.MaxBodyBytes ceiling of 64 MiB in any case).
const multipartBodyLimit int64 = 50 << 20

// multipartPartOverhead approximates the per-part framing bytes (boundary line
// and part headers, excluding the field name and filename, which are counted
// exactly) used to pre-size the body buffer, so the retained backing array is
// ~1×N instead of the ~1.6×N bytes.Buffer doubling would leave.
const multipartPartOverhead = 1 << 10

// multipartPartSummary is the --dry-run view of one multipart part. It carries
// only metadata — never a field value or file content — so a plan is safe to
// paste into an LLM context or a ticket. Bytes is the part's payload length
// for both kinds: the multipart framing is deterministic, so the exact
// total_bytes would reveal the summed field-value length anyway, and the
// caller already knows its own values.
type multipartPartSummary struct {
	Name        string `json:"name"`
	Kind        string `json:"kind"` // "field" | "file"
	Filename    string `json:"filename,omitempty"`
	ContentType string `json:"content_type,omitempty"`
	Bytes       int64  `json:"bytes"`
}

// multipartSummary is the "body" block of the execute --dry-run plan for a
// multipart request. TotalBytes is the exact assembled body length (what the
// broker's cap is measured against).
type multipartSummary struct {
	Kind       string                 `json:"kind"` // always "multipart/form-data"
	Parts      []multipartPartSummary `json:"parts"`
	TotalBytes int64                  `json:"total_bytes"`
}

// multipartBody is an assembled --form/--form-file body: the bytes (kept a
// *bytes.Buffer so the SDK transport's retry rewind can replay it via
// http.Request.GetBody without a second copy), the boundary-carrying
// Content-Type, and the safe dry-run summary.
type multipartBody struct {
	Body        *bytes.Buffer
	ContentType string
	Summary     multipartSummary
}

// formFileSpec is a parsed --form-file value:
//
//	key=@path[;type=<media-type>][;filename=<name>]
type formFileSpec struct {
	key         string
	path        string
	filename    string // override; empty = basename of path
	contentType string // override; empty = inferred from the extension
}

// formFileUsage is the actionable hint shared by every malformed --form-file
// value.
const formFileUsage = "Pass --form-file as key=@path, optionally suffixed curl-style with ;type=<media-type> and/or ;filename=<name> " +
	"(e.g. --form-file images=@face.jpg or --form-file doc=@blob;type=application/pdf;filename=report.pdf). " +
	"Quote a path that itself ends in ;type=… or ;filename=… as key=@\"path\"."

// multipartUsageError builds the coded usage error for a malformed
// --form/--form-file value (ARCH-4: agent-causable input surfaces a machine
// error_code).
func multipartUsageError(msg, actionable string) error {
	return &ux.CodedError{Code: ux.CodeMissingArgument, Msg: msg, Actionable: actionable}
}

// rejectHeaderBreak refuses CR/LF in a value that lands in a part's MIME
// header. mime/multipart escapes only `\` and `"` in Content-Disposition
// parameters (multipart.FileContentDisposition / escapeQuotes), so a CR/LF in a
// field name or filename would end the header line and let the caller inject
// arbitrary headers (or a premature body) into that part. The caller already
// controls the body, so no trust boundary is crossed — this is defense in depth
// for agent-assembled invocations.
func rejectHeaderBreak(flag, what, value string) error {
	if !strings.ContainsAny(value, "\r\n") {
		return nil
	}
	return multipartUsageError(
		fmt.Sprintf("%s %s %q contains a CR or LF character, which would break the multipart part header", flag, what, value),
		fmt.Sprintf("Remove the line break from the %s %s.", flag, what),
	)
}

// parseFormFileSpec parses one --form-file value. Option suffixes are parsed
// from the RIGHT: only trailing `;type=…` / `;filename=…` segments are options,
// and any other `;` stays part of the path (so `@a;b.txt` uploads "a;b.txt").
// A path that itself ends in an option-looking segment is wrapped in double
// quotes (`key=@"odd;type=x"`); after a quoted path every `;` segment must be a
// known option. A quoted path cannot contain `";`, and a leading `"` with no
// matching close is a literal part of the path (see cutQuotedPath). Option
// values cannot contain `;`. `@-` (stdin) is rejected:
// stdin may be an idle inherited stream that never sends EOF (#1354), so
// execute never reads it for a file part.
func parseFormFileSpec(kv string) (formFileSpec, error) {
	key, spec, ok := strings.Cut(kv, "=")
	if !ok || strings.TrimSpace(key) == "" || !strings.HasPrefix(spec, "@") {
		return formFileSpec{}, multipartUsageError(fmt.Sprintf("invalid --form-file value %q; expected key=@path", kv), formFileUsage)
	}
	if err := rejectHeaderBreak("--form-file", "field name", key); err != nil {
		return formFileSpec{}, err
	}
	out := formFileSpec{key: key}
	rest := spec[1:]

	var options []string
	if quoted, tail, isQuoted := cutQuotedPath(rest); isQuoted {
		out.path = quoted
		if tail != "" {
			options = strings.Split(tail[1:], ";")
		}
	} else {
		// Peel recognized options off the right; stop at the first segment that
		// is not one so a `;` inside the path is preserved.
		for {
			i := strings.LastIndex(rest, ";")
			if i < 0 || !isFormFileOption(rest[i+1:]) {
				break
			}
			options = append([]string{rest[i+1:]}, options...)
			rest = rest[:i]
		}
		out.path = rest
	}

	for _, opt := range options {
		if err := out.applyOption(kv, opt); err != nil {
			return formFileSpec{}, err
		}
	}

	switch out.path {
	case "":
		return formFileSpec{}, multipartUsageError(fmt.Sprintf("invalid --form-file value %q; expected key=@path", kv), formFileUsage)
	case "-":
		return formFileSpec{}, multipartUsageError(
			fmt.Sprintf("--form-file %s=@- (stdin) is not supported", key),
			fmt.Sprintf("Write the content to a file first, then pass --form-file %s=@<path> (add ;filename=<name> to control the uploaded name).", key),
		)
	}
	if out.filename == "" {
		if err := rejectHeaderBreak("--form-file", "filename", filepath.Base(out.path)); err != nil {
			return formFileSpec{}, multipartUsageError(
				fmt.Sprintf("--form-file %s path %q has a CR or LF in its file name, which would break the multipart part header", key, out.path),
				fmt.Sprintf("Set a clean upload name with --form-file %s=@<path>;filename=<name>.", key),
			)
		}
	}
	return out, nil
}

// cutQuotedPath splits a `"path"[;options]` spec. A leading `"` is quoting
// only when a closing `"` follows that is itself followed by end of string or
// `;` (the first such quote closes the path); otherwise the spec is not quoted
// and the caller parses it as a literal path, so a real file name that starts
// with `"` still works unquoted. tail is "" or starts with `;`.
func cutQuotedPath(rest string) (path, tail string, ok bool) {
	if !strings.HasPrefix(rest, `"`) {
		return "", "", false
	}
	for i := 1; i < len(rest); i++ {
		if rest[i] != '"' {
			continue
		}
		if after := rest[i+1:]; after == "" || strings.HasPrefix(after, ";") {
			return rest[1:i], after, true
		}
	}
	return "", "", false
}

// isFormFileOption reports whether seg is a recognized `type=`/`filename=`
// option segment (case-insensitive name, like curl).
func isFormFileOption(seg string) bool {
	name, _, ok := strings.Cut(seg, "=")
	if !ok {
		return false
	}
	switch strings.ToLower(name) {
	case "type", "filename":
		return true
	}
	return false
}

// applyOption applies one `name=value` option segment to the spec.
func (s *formFileSpec) applyOption(kv, opt string) error {
	name, value, ok := strings.Cut(opt, "=")
	if !ok || !isFormFileOption(opt) {
		return multipartUsageError(fmt.Sprintf("--form-file value %q has an unknown option %q (supported: type, filename)", kv, opt), formFileUsage)
	}
	name = strings.ToLower(name)
	if value == "" {
		return multipartUsageError(fmt.Sprintf("--form-file value %q has an empty %s= option", kv, name), formFileUsage)
	}
	switch name {
	case "type":
		if s.contentType != "" {
			return multipartUsageError(fmt.Sprintf("--form-file value %q sets type= more than once", kv), formFileUsage)
		}
		// Parse-and-reformat: the value lands verbatim in the part's
		// Content-Type header, so only a well-formed type/subtype (whose
		// grammar excludes CR/LF) is accepted.
		mediaType, params, err := mime.ParseMediaType(value)
		formatted := ""
		if err == nil && strings.Contains(mediaType, "/") {
			formatted = mime.FormatMediaType(mediaType, params)
		}
		if formatted == "" {
			return multipartUsageError(
				fmt.Sprintf("--form-file value %q has an invalid type=%q; expected a media type like image/png", kv, value),
				formFileUsage)
		}
		s.contentType = formatted
	case "filename":
		if s.filename != "" {
			return multipartUsageError(fmt.Sprintf("--form-file value %q sets filename= more than once", kv), formFileUsage)
		}
		if err := rejectHeaderBreak("--form-file", "filename", value); err != nil {
			return err
		}
		s.filename = value
	}
	return nil
}

// partContentType resolves the file part's Content-Type: an explicit ;type=
// wins, then the path's extension, then the ;filename= override's extension,
// then application/octet-stream. Unlike multipart.Writer.CreateFormFile (which
// hardcodes octet-stream), inferring lets MIME-validating upload APIs
// (image/jpeg, application/pdf, …) accept the part.
func (s formFileSpec) partContentType() string {
	if s.contentType != "" {
		return s.contentType
	}
	if ct := mime.TypeByExtension(filepath.Ext(s.path)); ct != "" {
		return ct
	}
	if s.filename != "" {
		if ct := mime.TypeByExtension(filepath.Ext(s.filename)); ct != "" {
			return ct
		}
	}
	return "application/octet-stream"
}

// uploadName is the filename sent in the part's Content-Disposition.
func (s formFileSpec) uploadName() string {
	if s.filename != "" {
		return s.filename
	}
	return filepath.Base(s.path)
}

// formField is a parsed --form key=value.
type formField struct{ key, value string }

// parseFormField parses one --form value.
func parseFormField(kv string) (formField, error) {
	key, value, ok := strings.Cut(kv, "=")
	if !ok || strings.TrimSpace(key) == "" {
		return formField{}, badFlagKV("--form", kv)
	}
	if err := rejectHeaderBreak("--form", "field name", key); err != nil {
		return formField{}, err
	}
	// curl's -F treats a leading @ as a file reference; here file parts live
	// on --form-file, so a @-prefixed --form value is almost always a curl
	// habit that would silently send the literal string as text. Fail closed;
	// a doubled @@ is the escape hatch for an intentional literal leading @.
	if strings.HasPrefix(value, "@@") {
		value = value[1:]
	} else if strings.HasPrefix(value, "@") {
		return formField{}, multipartUsageError(
			fmt.Sprintf("--form value for %q starts with @; --form sends the literal text, file parts use --form-file", key),
			fmt.Sprintf("Upload a file with --form-file %s=@path, or send a literal leading @ by doubling it (--form %s=@@…).", key, key),
		)
	}
	return formField{key: key, value: value}, nil
}

// errMultipartTooLarge is the internal sentinel the bounded file copy returns
// when a file grows past the remaining budget mid-read.
var errMultipartTooLarge = errors.New("multipart body exceeds limit")

// multipartTooLargeError is the coded refusal for a body over the limit.
// totalBytes is a lower bound when the overflow was detected mid-read. The
// code is MISSING_ARGUMENT (the closed enum has no size code), so
// details.reason carries the stable "body_too_large" discriminator agents
// branch on.
func multipartTooLargeError(totalBytes, limit int64) error {
	return &ux.CodedError{
		Code: ux.CodeMissingArgument,
		Msg: fmt.Sprintf("multipart body is %d bytes, over the broker's %d-byte (%d MiB) multipart/form-data limit",
			totalBytes, limit, limit>>20),
		Actionable: "Upload smaller files (or fewer --form-file parts per call); the broker refuses multipart bodies over this size with 413.",
		Details:    map[string]any{"reason": "body_too_large", "total_bytes": totalBytes, "limit_bytes": limit},
	}
}

// notRegularFileError refuses a --form-file path that is not a regular file. A
// FIFO blocks in open until a writer appears, and /dev/stdin, /dev/fd/0 and
// friends resolve to the inherited stdin, which (when it is a pipe or terminal)
// may never send EOF (#1354). Those aliases pass only when stdin is redirected
// from a regular file, where the stat sees that file and the read ends. The body is
// assembled before any request context exists, so neither --timeout nor
// cancellation could interrupt either hang.
func notRegularFileError(path, key string) error {
	return multipartUsageError(
		fmt.Sprintf("--form-file %s is not a regular file (pipe, device, or stdin alias)", path),
		fmt.Sprintf("Write the content to a regular file first, then pass --form-file %s=@<path>.", key),
	)
}

// buildMultipartBody assembles a multipart/form-data body from --form text
// fields (key=value) and --form-file file parts (key=@path[;type=…][;filename=…])
// (#1316, #1320). Every value is parsed and every file stat'd BEFORE any file
// is read, so a malformed flag or an oversize upload fails fast without
// buffering anything. The broker is byte-transparent for the request body, so
// once the boundary Content-Type is set the multipart bytes reach the upstream
// intact with the credential still injected on the headers.
func buildMultipartBody(form, formFile []string, limit int64) (*multipartBody, error) {
	fields := make([]formField, 0, len(form))
	for _, kv := range form {
		f, err := parseFormField(kv)
		if err != nil {
			return nil, err
		}
		fields = append(fields, f)
	}
	files := make([]formFileSpec, 0, len(formFile))
	for _, kv := range formFile {
		s, err := parseFormFileSpec(kv)
		if err != nil {
			return nil, err
		}
		files = append(files, s)
	}

	// Pre-flight: stat every file and sum the payload bytes (values, file
	// sizes, and the field names/filenames that land in part headers) against
	// the limit before reading anything. Only regular files are accepted, so
	// every stat size is meaningful; the bounded copy below still catches a
	// file that grows after the stat.
	var payload int64
	for _, f := range fields {
		payload += int64(len(f.key) + len(f.value))
	}
	for _, s := range files {
		fi, err := os.Stat(s.path)
		if err != nil {
			// A wrong path is agent-causable input (ARCH-4).
			actionable := fmt.Sprintf("Check that the file exists and is readable, then retry with --form-file %s=@<path>.", s.key)
			if lp := strings.ToLower(s.path); strings.Contains(lp, ";type=") || strings.Contains(lp, ";filename=") {
				// The suffix was kept as part of the path because a later
				// segment is not an option (e.g. a type= with ;-parameters).
				actionable += " Option values cannot contain ';' (type= takes a bare type/subtype), and only trailing ;type=/;filename= segments are parsed as options."
			}
			return nil, multipartUsageError(fmt.Sprintf("cannot read --form-file %s: %v", s.path, err), actionable)
		}
		if fi.IsDir() {
			return nil, multipartUsageError(
				fmt.Sprintf("--form-file %s is a directory; expected a file", s.path),
				fmt.Sprintf("Point --form-file %s=@<path> at a regular file.", s.key),
			)
		}
		if !fi.Mode().IsRegular() {
			return nil, notRegularFileError(s.path, s.key)
		}
		payload += int64(len(s.key)+len(s.uploadName())) + fi.Size()
	}
	if payload > limit {
		return nil, multipartTooLargeError(payload, limit)
	}

	buf := new(bytes.Buffer)
	buf.Grow(int(min(payload+int64(multipartPartOverhead*(len(fields)+len(files)+1)), limit+multipartPartOverhead)))
	w := multipart.NewWriter(buf)
	summary := multipartSummary{Kind: "multipart/form-data", Parts: make([]multipartPartSummary, 0, len(fields)+len(files))}

	for _, f := range fields {
		if err := w.WriteField(f.key, f.value); err != nil {
			return nil, fmt.Errorf("write form field %q: %w", f.key, err)
		}
		summary.Parts = append(summary.Parts, multipartPartSummary{Name: f.key, Kind: "field", Bytes: int64(len(f.value))})
	}

	for _, s := range files {
		ct := s.partContentType()
		name := s.uploadName()
		h := make(textproto.MIMEHeader)
		h.Set("Content-Disposition", multipart.FileContentDisposition(s.key, name))
		h.Set("Content-Type", ct)
		part, err := w.CreatePart(h)
		if err != nil {
			return nil, fmt.Errorf("create form file %q: %w", s.key, err)
		}
		n, err := copyFilePart(part, s, limit-int64(buf.Len()))
		if errors.Is(err, errMultipartTooLarge) {
			return nil, multipartTooLargeError(int64(buf.Len()), limit)
		}
		if err != nil {
			return nil, err
		}
		summary.Parts = append(summary.Parts, multipartPartSummary{Name: s.key, Kind: "file", Filename: name, ContentType: ct, Bytes: n})
	}

	if err := w.Close(); err != nil {
		return nil, fmt.Errorf("finalize multipart body: %w", err)
	}
	// Exact check: the broker caps the whole body, framing included.
	if int64(buf.Len()) > limit {
		return nil, multipartTooLargeError(int64(buf.Len()), limit)
	}
	summary.TotalBytes = int64(buf.Len())
	return &multipartBody{Body: buf, ContentType: w.FormDataContentType(), Summary: summary}, nil
}

// copyFilePart streams s.path into part, refusing to copy more than budget
// bytes (a file that grew since the pre-flight stat). The opened handle is
// re-checked as a regular file, narrowing the window in which the path could be
// swapped for a pipe or device after the stat. Streaming avoids a second
// whole-file copy; the body still accumulates in the caller's buffer so the
// request stays replayable for the SDK transport's retry rewind (an io.Pipe
// stream would break that).
func copyFilePart(part io.Writer, s formFileSpec, budget int64) (int64, error) {
	readErr := func(err error) error {
		return multipartUsageError(
			fmt.Sprintf("cannot read --form-file %s: %v", s.path, err),
			fmt.Sprintf("Check that the file exists and is readable, then retry with --form-file %s=@<path>.", s.key),
		)
	}
	f, err := os.Open(s.path)
	if err != nil {
		return 0, readErr(err)
	}
	defer func() { _ = f.Close() }()
	fi, err := f.Stat()
	if err != nil {
		return 0, readErr(err)
	}
	if !fi.Mode().IsRegular() {
		return 0, notRegularFileError(s.path, s.key)
	}
	if budget < 0 {
		budget = 0
	}
	n, err := io.Copy(part, io.LimitReader(f, budget+1))
	if err != nil {
		return n, readErr(err)
	}
	if n > budget {
		return n, errMultipartTooLarge
	}
	return n, nil
}
