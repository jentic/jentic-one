package api

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

func (a *app) printAPIList(apis []registeredAPI) {
	fmt.Fprintln(a.Out, theme.Heading.Render("APIs"))
	if len(apis) == 0 {
		fmt.Fprintln(a.Out, cmdcore.DotDown()+" "+theme.Dim.Render("no APIs imported yet — try `jentic catalog`"))
		return
	}
	for _, api := range apis {
		fmt.Fprintln(a.Out, apiRow(api))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("%d API(s)", len(apis))))
}

// apiRow renders one API: a live/draft dot, the accent identity, and a dim
// operation count.
func apiRow(api registeredAPI) string {
	dot := cmdcore.DotDown()
	if api.CurrentRevisionID != "" {
		dot = cmdcore.DotOK()
	}
	row := dot + " " + theme.Accent.Render(apiRefLabel(api.API))
	if api.OperationCount > 0 {
		row += "  " + theme.Dim.Render(fmt.Sprintf("%d ops", api.OperationCount))
	}
	if api.DisplayName != "" {
		row += "  " + theme.Dim.Render(api.DisplayName)
	}
	return row
}

func (a *app) printAPIDetail(api *registeredAPI) {
	fmt.Fprintln(a.Out, theme.Heading.Render(apiRefLabel(api.API)))
	if api.DisplayName != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("name", api.DisplayName))
	}
	if api.Description != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("description", api.Description))
	}
	if api.API.Host != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("host", api.API.Host))
	}
	state := "no live revision"
	dot := cmdcore.DotDown()
	if api.CurrentRevisionID != "" {
		state, dot = "live: "+api.CurrentRevisionID, cmdcore.DotOK()
	}
	fmt.Fprintln(a.Out, "  "+dot+" "+theme.Field("current", state))
	fmt.Fprintln(a.Out, "  "+theme.Field("revisions", strconv.Itoa(api.RevisionCount)))
	fmt.Fprintln(a.Out, "  "+theme.Field("operations", strconv.Itoa(api.OperationCount)))
	if len(api.SecuritySchemes) > 0 {
		fmt.Fprintln(a.Out, "  "+theme.Field("auth", strings.Join(api.SecuritySchemes, ", ")))
	}
}

func (a *app) printOperations(ops []apiOperation, hasMore bool) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Heading.Render("Operations"))
	if len(ops) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no operations"))
		return
	}
	for _, op := range ops {
		fmt.Fprintln(a.Out, "  "+apiOpLine(op))
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("… more (use --limit or operations --all)"))
	}
}

// apiOpLine renders "METHOD  path  name" with the method tinted.
func apiOpLine(op apiOperation) string {
	line := theme.Accent.Render(fmt.Sprintf("%-6s", op.Method)) + " " + theme.Command.Render(op.Path)
	label := op.Name
	if label == "" {
		label = op.Description
	}
	if label != "" {
		line += "  " + theme.Dim.Render(label)
	}
	if op.Deprecated {
		line += "  " + theme.Warnf("(deprecated)")
	}
	return line
}

func (a *app) printRevisions(ref string, revs []apiRevision) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Revisions")+theme.Dim.Render("  "+ref))
	if len(revs) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no revisions"))
		return
	}
	for _, rev := range revs {
		fmt.Fprintln(a.Out, "  "+revisionLine(rev))
	}
}

func revisionLine(rev apiRevision) string {
	dot := cmdcore.DotDown()
	switch {
	case rev.IsCurrent:
		dot = cmdcore.DotOK()
	case rev.State == "draft":
		dot = cmdcore.DotWarn()
	}
	line := dot + " " + theme.Accent.Render(rev.RevisionID) + "  " + theme.Field("state", rev.State)
	if rev.IsCurrent {
		line += "  " + theme.Success.Render("(current)")
	}
	if rev.OperationCount > 0 {
		line += "  " + theme.Dim.Render(fmt.Sprintf("%d ops", rev.OperationCount))
	}
	return line
}

func apiRefLabel(ref apiRef) string {
	return ref.Vendor + "/" + ref.Name + "/" + ref.Version
}
