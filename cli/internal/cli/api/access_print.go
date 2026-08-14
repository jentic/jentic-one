package api

import (
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

func (a *app) printMe(me *control.MeAgent) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Identity"))
	fmt.Fprintln(a.Out, "  "+theme.Field("agent", me.Id))
	if me.Name != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("name", me.Name))
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("status", me.Status))
	scopes := "none"
	if len(me.Scopes) > 0 {
		scopes = strings.Join(me.Scopes, ", ")
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("scopes", scopes))
	if stale := staleScopes(me.Scopes, me.TokenScopes); len(stale) > 0 {
		fmt.Fprintln(a.Out, "  "+theme.Warnf("granted but not yet on your token: %s", strings.Join(stale, ", ")))
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("run `jentic access refresh` to pick them up"))
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Toolkit bindings"))
	if len(me.ToolkitBindings) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("none — you cannot execute yet; run `jentic access request --toolkit <vendor/name>`"))
	} else {
		for _, b := range me.ToolkitBindings {
			if name := deref(b.Name); name != "" {
				fmt.Fprintln(a.Out, "  "+theme.Command.Render(name)+"  "+theme.Dim.Render(b.ToolkitId))
			} else {
				fmt.Fprintln(a.Out, "  "+theme.Command.Render(b.ToolkitId))
			}
		}
	}

	// whoami is the control-plane view of "what can I do?" (scopes + toolkit
	// bindings above). There is no per-directory access surface for a plain
	// agent — `context view` shows only environment/identity/mode — so point at
	// doctor for local-setup health rather than a command that surfaces nothing.
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("Toolkit bindings above are your control-plane access. Run `jentic doctor` to check your local setup."))
}

func (a *app) printRequestList(reqs []control.AccessRequestResponse, hasMore bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Access Requests"))
	if len(reqs) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no requests"))
		return
	}
	for i := range reqs {
		r := &reqs[i]
		fmt.Fprintln(a.Out, "  "+theme.Command.Render(r.Id)+"  "+statusStyle(r.Status))
		for j := range r.Items {
			fmt.Fprintln(a.Out, "    "+theme.Dim.Render(itemSummary(&r.Items[j])))
		}
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("… more available (use --all or --cursor)"))
	}
}

func (a *app) printRequest(r *control.AccessRequestResponse, showApprove bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Access Request"))
	fmt.Fprintln(a.Out, "  "+theme.Field("id", r.Id))
	fmt.Fprintln(a.Out, "  "+theme.Dim.Render(fmt.Sprintf("%-9s ", "status:"))+statusStyle(r.Status))
	if reason := deref(r.Reason); reason != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("reason", reason))
	}
	for i := range r.Items {
		it := &r.Items[i]
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render(itemSummary(it))+"  "+statusStyle(it.Status))
		// A denied item carries the reason it couldn't be granted (e.g. "No
		// toolkit serves API …; provision and bind a credential for it first").
		// Surface it so the agent/operator learns what to fix; JSON output
		// already includes decision_reason.
		if reason := deref(it.DecisionReason); reason != "" {
			fmt.Fprintln(a.Out, "    "+theme.Warn.Render(reason))
		}
	}
	if showApprove && r.Status == statusPending && r.ApproveUrl != "" {
		fmt.Fprintln(a.Out, "\n  "+theme.Info.Render("Share this with your operator to approve:"))
		fmt.Fprintln(a.Out, "  "+theme.Command.Render(r.ApproveUrl))
	}
}

func itemSummary(it *control.AccessRequestItemResponse) string {
	target := deref(it.ResourceId)
	if target == "" && it.ResourceReference != nil {
		ref := *it.ResourceReference
		vendor, _ := ref["vendor"].(string)
		name, _ := ref["name"].(string)
		target = strings.Trim(vendor+"/"+name, "/")
	}
	return fmt.Sprintf("%s:%s %s", it.ResourceType, it.Action, target)
}

func statusStyle(status string) string {
	switch status {
	case statusApproved:
		return theme.Success.Render(status)
	case statusDenied, statusExpired, statusWithdrawn:
		return theme.Warn.Render(status)
	default:
		return theme.Accent.Render(status)
	}
}
