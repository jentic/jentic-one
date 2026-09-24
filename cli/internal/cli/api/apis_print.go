package api

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/theme"
)

func (a *app) printAPIList(ctx context.Context, apis []registeredAPI) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("APIs"))
	if len(apis) == 0 {
		fmt.Fprintln(a.Out, st.DotDown()+" "+st.Dim.Render("no APIs imported yet — try `jentic catalog`"))
		return
	}
	for _, api := range apis {
		fmt.Fprintln(a.Out, apiRow(st, api))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, st.Dim.Render(fmt.Sprintf("%d API(s)", len(apis))))
}

// apiRow renders one API: a live/draft dot, the accent identity, and a dim
// operation count.
func apiRow(st theme.Styles, api registeredAPI) string {
	dot := st.DotDown()
	if api.CurrentRevisionID != "" {
		dot = st.DotOK()
	}
	row := dot + " " + st.Accent.Render(apiRefLabel(api.API))
	if api.OperationCount > 0 {
		row += "  " + st.Dim.Render(fmt.Sprintf("%d ops", api.OperationCount))
	}
	if api.DisplayName != "" {
		row += "  " + st.Dim.Render(api.DisplayName)
	}
	return row
}

func (a *app) printAPIDetail(ctx context.Context, api *registeredAPI) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render(apiRefLabel(api.API)))
	if api.DisplayName != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("name", api.DisplayName))
	}
	if api.Description != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("description", api.Description))
	}
	if api.API.Host != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("host", api.API.Host))
	}
	state := "no live revision"
	dot := st.DotDown()
	if api.CurrentRevisionID != "" {
		state, dot = "live: "+api.CurrentRevisionID, st.DotOK()
	}
	fmt.Fprintln(a.Out, "  "+dot+" "+st.Field("current", state))
	fmt.Fprintln(a.Out, "  "+st.Field("revisions", strconv.Itoa(api.RevisionCount)))
	fmt.Fprintln(a.Out, "  "+st.Field("operations", strconv.Itoa(api.OperationCount)))
	if len(api.SecuritySchemes) > 0 {
		fmt.Fprintln(a.Out, "  "+st.Field("auth", strings.Join(api.SecuritySchemes, ", ")))
	}
}

func (a *app) printOperations(ctx context.Context, ops []apiOperation, hasMore bool) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, st.Heading.Render("Operations"))
	if len(ops) == 0 {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("no operations"))
		return
	}
	for _, op := range ops {
		fmt.Fprintln(a.Out, "  "+apiOpLine(st, op))
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("… more (use --limit or operations --all)"))
	}
}

// apiOpLine renders "METHOD  path  name" with the method tinted.
func apiOpLine(st theme.Styles, op apiOperation) string {
	line := st.Accent.Render(fmt.Sprintf("%-6s", op.Method)) + " " + st.Command.Render(op.Path)
	label := op.Name
	if label == "" {
		label = op.Description
	}
	if label != "" {
		line += "  " + st.Dim.Render(label)
	}
	if op.Deprecated {
		line += "  " + st.Warnf("(deprecated)")
	}
	return line
}

func (a *app) printRevisions(ctx context.Context, ref string, revs []apiRevision) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("Revisions")+st.Dim.Render("  "+ref))
	if len(revs) == 0 {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("no revisions"))
		return
	}
	for _, rev := range revs {
		fmt.Fprintln(a.Out, "  "+revisionLine(st, rev))
	}
}

func revisionLine(st theme.Styles, rev apiRevision) string {
	dot := st.DotDown()
	switch {
	case rev.IsCurrent:
		dot = st.DotOK()
	case rev.State == "draft":
		dot = st.DotWarn()
	}
	line := dot + " " + st.Accent.Render(rev.RevisionID) + "  " + st.Field("state", rev.State)
	if rev.IsCurrent {
		line += "  " + st.Success.Render("(current)")
	}
	if rev.OperationCount > 0 {
		line += "  " + st.Dim.Render(fmt.Sprintf("%d ops", rev.OperationCount))
	}
	return line
}

func apiRefLabel(ref apiRef) string {
	return ref.Vendor + "/" + ref.Name + "/" + ref.Version
}
