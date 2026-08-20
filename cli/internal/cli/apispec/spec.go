// Package apispec parses an OpenAPI document (the embedded vendored control spec,
// or a --live one fetched from the server) to back the `jentic api` passthrough:
// path-allowlist matching, operation listing, and per-operation self-description
// (`api describe`). It reuses libopenapi — the same parser tools/specgen uses — so
// the module carries exactly one spec-parsing dependency (impl/5.0 §6a).
package apispec

import (
	"fmt"
	"io"
	"log/slog"
	"regexp"
	"sort"
	"strings"

	"github.com/pb33f/libopenapi"
	"github.com/pb33f/libopenapi/datamodel"
	base "github.com/pb33f/libopenapi/datamodel/high/base"
	v3 "github.com/pb33f/libopenapi/datamodel/high/v3"
	"github.com/pb33f/libopenapi/orderedmap"
)

// Spec is a parsed OpenAPI document with a compiled route table for concrete-path
// matching.
type Spec struct {
	Version string
	routes  []route
}

// route is one templated spec path compiled to a regexp for concrete-path
// matching plus its per-method operations.
type route struct {
	template string
	re       *regexp.Regexp
	params   []string
	methods  map[string]*v3.Operation
}

// Operation is the self-description of a single spec operation, JSON-serializable
// for `api describe` in agent mode.
type Operation struct {
	Method         string   `json:"method"`
	Path           string   `json:"path"`
	OperationID    string   `json:"operation_id,omitempty"`
	Summary        string   `json:"summary,omitempty"`
	PathParams     []Param  `json:"path_params,omitempty"`
	QueryParams    []Param  `json:"query_params,omitempty"`
	RequestBody    any      `json:"request_body_schema,omitempty"`
	RequiredFields []string `json:"required_fields,omitempty"`
	ResponseSchema any      `json:"response_schema,omitempty"`
}

// Param is a query/path parameter description.
type Param struct {
	Name     string `json:"name"`
	Required bool   `json:"required"`
	Type     string `json:"type,omitempty"`
	Desc     string `json:"description,omitempty"`
}

// Parse builds a Spec from raw OpenAPI bytes (YAML or JSON). External $refs are
// left unresolved (hermetic; the control spec has none that matter for our use)
// and libopenapi's logger is silenced.
func Parse(specBytes []byte) (*Spec, error) {
	doc, err := libopenapi.NewDocumentWithConfiguration(specBytes, &datamodel.DocumentConfiguration{
		SkipExternalRefResolution: true,
		Logger:                    slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err != nil {
		return nil, fmt.Errorf("parsing spec: %w", err)
	}
	model, err := doc.BuildV3Model()
	if err != nil {
		return nil, fmt.Errorf("building spec model: %w", err)
	}

	s := &Spec{}
	if model.Model.Info != nil {
		s.Version = model.Model.Info.Version
	}
	if model.Model.Paths == nil {
		return s, nil
	}
	for pair := model.Model.Paths.PathItems.First(); pair != nil; pair = pair.Next() {
		tmpl := pair.Key()
		item := pair.Value()
		re, params := compilePath(tmpl)
		r := route{template: tmpl, re: re, params: params, methods: map[string]*v3.Operation{}}
		for op := item.GetOperations().First(); op != nil; op = op.Next() {
			r.methods[strings.ToUpper(op.Key())] = op.Value()
		}
		s.routes = append(s.routes, r)
	}
	return s, nil
}

// pathParamRe matches an OpenAPI `{name}` path template segment.
var pathParamRe = regexp.MustCompile(`\{([^}]+)\}`)

// compilePath turns a templated spec path (/credentials/{id}) into an anchored
// regexp that matches a concrete path (/credentials/abc), plus the ordered param
// names. A `{param}` matches one non-slash segment; literal segments are regexp-
// quoted so path characters like '.' are matched literally.
func compilePath(tmpl string) (*regexp.Regexp, []string) {
	matches := pathParamRe.FindAllStringSubmatchIndex(tmpl, -1)
	params := make([]string, 0, len(matches))
	var b strings.Builder
	b.WriteString("^")
	last := 0
	for _, loc := range matches {
		// loc: [matchStart, matchEnd, groupStart, groupEnd]
		b.WriteString(regexp.QuoteMeta(tmpl[last:loc[0]])) // literal prefix
		params = append(params, tmpl[loc[2]:loc[3]])
		b.WriteString(`[^/]+`)
		last = loc[1]
	}
	b.WriteString(regexp.QuoteMeta(tmpl[last:]))
	b.WriteString("$")
	return regexp.MustCompile(b.String()), params
}

// Match resolves a concrete request path + method against the spec. It ignores any
// query string on reqPath. When multiple templated routes match, the most specific
// wins: a static route (no path params) beats a templated one, and among templated
// routes fewer params beats more (mirrors how HTTP routers disambiguate
// /credentials/providers from /credentials/{id}). Returns the matched Operation
// description, or ok=false if no route/method matches.
func (s *Spec) Match(method, reqPath string) (*Operation, bool) {
	method = strings.ToUpper(method)
	pathOnly := reqPath
	if i := strings.IndexByte(pathOnly, '?'); i >= 0 {
		pathOnly = pathOnly[:i]
	}
	var best *route
	for i := range s.routes {
		r := &s.routes[i]
		if !r.re.MatchString(pathOnly) {
			continue
		}
		if _, ok := r.methods[method]; !ok {
			continue
		}
		if best == nil || len(r.params) < len(best.params) {
			best = r
		}
	}
	if best == nil {
		return nil, false
	}
	return describe(method, best.template, best.methods[method]), true
}

// HasPath reports whether any route matches reqPath (regardless of method). Used
// to give a better error ("path exists, wrong method") than a bare not-found.
func (s *Spec) HasPath(reqPath string) bool {
	pathOnly := reqPath
	if i := strings.IndexByte(pathOnly, '?'); i >= 0 {
		pathOnly = pathOnly[:i]
	}
	for i := range s.routes {
		if s.routes[i].re.MatchString(pathOnly) {
			return true
		}
	}
	return false
}

// List returns every operation (method+path+summary), sorted by path then method,
// for `api ops`. filter, when non-empty, keeps only operations whose path,
// operationId, or summary contains it (case-insensitive).
func (s *Spec) List(filter string) []Operation {
	var out []Operation
	lf := strings.ToLower(filter)
	for i := range s.routes {
		r := &s.routes[i]
		for m, op := range r.methods {
			d := describe(m, r.template, op)
			if lf != "" && !strings.Contains(strings.ToLower(d.Path+" "+d.OperationID+" "+d.Summary), lf) {
				continue
			}
			// api ops is a shallow index; drop the heavy schema fields.
			out = append(out, Operation{Method: d.Method, Path: d.Path, OperationID: d.OperationID, Summary: d.Summary})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Path != out[j].Path {
			return out[i].Path < out[j].Path
		}
		return out[i].Method < out[j].Method
	})
	return out
}

// Describe returns the full self-description for a concrete method+path, or
// ok=false if unmatched.
func (s *Spec) Describe(method, reqPath string) (*Operation, bool) {
	return s.Match(method, reqPath)
}

// describe extracts the params + request/response schema for one operation.
func describe(method, tmpl string, op *v3.Operation) *Operation {
	d := &Operation{Method: method, Path: tmpl, OperationID: op.OperationId, Summary: op.Summary}
	for _, p := range op.Parameters {
		param := Param{Name: p.Name, Desc: p.Description}
		if p.Required != nil {
			param.Required = *p.Required
		}
		if p.Schema != nil && p.Schema.Schema() != nil {
			param.Type = strings.Join(p.Schema.Schema().Type, "|")
		}
		switch p.In {
		case "path":
			param.Required = true
			d.PathParams = append(d.PathParams, param)
		case "query":
			d.QueryParams = append(d.QueryParams, param)
		}
	}
	if op.RequestBody != nil {
		if mt := jsonMediaType(op.RequestBody.Content); mt != nil && mt.Schema != nil && mt.Schema.Schema() != nil {
			sch := mt.Schema.Schema()
			d.RequestBody = schemaSummary(sch)
			d.RequiredFields = sch.Required
		}
	}
	if op.Responses != nil {
		if resp := successResponse(op); resp != nil {
			if mt := jsonMediaType(resp.Content); mt != nil && mt.Schema != nil && mt.Schema.Schema() != nil {
				d.ResponseSchema = schemaSummary(mt.Schema.Schema())
			}
		}
	}
	return d
}

// jsonMediaType returns the application/json media type from a content map, if any.
func jsonMediaType(content *orderedmap.Map[string, *v3.MediaType]) *v3.MediaType {
	if content == nil {
		return nil
	}
	for pair := content.First(); pair != nil; pair = pair.Next() {
		if strings.Contains(pair.Key(), "json") {
			return pair.Value()
		}
	}
	return nil
}

// successResponse returns the 2xx response (preferring 200/201) for an operation.
func successResponse(op *v3.Operation) *v3.Response {
	codes := op.Responses.Codes
	if codes == nil {
		return nil
	}
	for _, want := range []string{"200", "201", "202"} {
		if r, ok := codes.Get(want); ok {
			return r
		}
	}
	for pair := codes.First(); pair != nil; pair = pair.Next() {
		if strings.HasPrefix(pair.Key(), "2") {
			return pair.Value()
		}
	}
	return nil
}

// schemaSummary renders a shallow, JSON-serializable summary of a schema: its
// type, a one-level map of property name -> type, and required fields. It is
// deliberately NOT a full recursive dump — `api describe` is a discovery aid, not
// a schema exporter, and the control spec's schemas are large. A caller wanting
// the full schema uses --live against /openapi.json directly.
func schemaSummary(sch *base.Schema) map[string]any {
	if sch == nil {
		return nil
	}
	out := map[string]any{}
	if len(sch.Type) > 0 {
		out["type"] = strings.Join(sch.Type, "|")
	}
	if sch.Description != "" {
		out["description"] = sch.Description
	}
	if len(sch.Required) > 0 {
		out["required"] = sch.Required
	}
	if sch.Properties != nil && sch.Properties.Len() > 0 {
		props := map[string]string{}
		for pair := sch.Properties.First(); pair != nil; pair = pair.Next() {
			ps := pair.Value()
			t := "object"
			if ps != nil && ps.Schema() != nil && len(ps.Schema().Type) > 0 {
				t = strings.Join(ps.Schema().Type, "|")
			}
			props[pair.Key()] = t
		}
		out["properties"] = props
	}
	return out
}
