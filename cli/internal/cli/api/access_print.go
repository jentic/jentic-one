package api

import (
	"context"
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

func (a *app) printMe(ctx context.Context, me *control.MeAgent) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("Identity"))
	fmt.Fprintln(a.Out, "  "+st.Field("agent", me.Id))
	if me.Name != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("name", me.Name))
	}
	fmt.Fprintln(a.Out, "  "+st.Field("status", me.Status))
	scopes := "none"
	if len(me.Scopes) > 0 {
		scopes = strings.Join(me.Scopes, ", ")
	}
	fmt.Fprintln(a.Out, "  "+st.Field("scopes", scopes))
	if stale := staleScopes(me.Scopes, me.TokenScopes); len(stale) > 0 {
		fmt.Fprintln(a.Out, "  "+st.Warnf("granted but not yet on your token: %s", strings.Join(stale, ", ")))
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("run `jentic access refresh` to pick them up"))
	}

	fmt.Fprintln(a.Out, st.Heading.Render("Toolkit bindings"))
	if len(me.ToolkitBindings) == 0 {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("none — you cannot execute yet; run `jentic access request --toolkit <vendor/name>`"))
	} else {
		for _, b := range me.ToolkitBindings {
			if name := deref(b.Name); name != "" {
				fmt.Fprintln(a.Out, "  "+st.Command.Render(name)+"  "+st.Dim.Render(b.ToolkitId))
			} else {
				fmt.Fprintln(a.Out, "  "+st.Command.Render(b.ToolkitId))
			}
		}
	}

	// whoami is the control-plane view of "what can I do?" (scopes + toolkit
	// bindings above). There is no per-directory access surface for a plain
	// agent — `context view` shows only environment/identity/mode — so point at
	// doctor for local-setup health rather than a command that surfaces nothing.
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, st.Dim.Render("Toolkit bindings above are your control-plane access. Run `jentic doctor` to check your local setup."))
}

func (a *app) printRequestList(ctx context.Context, reqs []control.AccessRequestResponse, hasMore bool) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("Access Requests"))
	if len(reqs) == 0 {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("no requests"))
		return
	}
	for i := range reqs {
		r := &reqs[i]
		fmt.Fprintln(a.Out, "  "+st.Command.Render(r.Id)+"  "+statusStyle(st, r.Status))
		for j := range r.Items {
			fmt.Fprintln(a.Out, "    "+st.Dim.Render(itemSummary(&r.Items[j])))
		}
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("… more available (use --all or --cursor)"))
	}
}

func (a *app) printRequest(ctx context.Context, r *control.AccessRequestResponse, showApprove bool) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("Access Request"))
	fmt.Fprintln(a.Out, "  "+st.Field("id", r.Id))
	fmt.Fprintln(a.Out, "  "+st.Dim.Render(fmt.Sprintf("%-9s ", "status:"))+statusStyle(st, r.Status))
	if reason := deref(r.Reason); reason != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("reason", reason))
	}
	for i := range r.Items {
		it := &r.Items[i]
		fmt.Fprintln(a.Out, "  "+st.Dim.Render(itemSummary(it))+"  "+statusStyle(st, it.Status))
		// A denied item carries the reason it couldn't be granted (e.g. "No
		// toolkit serves API …; provision and bind a credential for it first").
		// Surface it so the agent/operator learns what to fix; JSON output
		// already includes decision_reason.
		if reason := deref(it.DecisionReason); reason != "" {
			fmt.Fprintln(a.Out, "    "+st.Warn.Render(reason))
		}
	}
	if showApprove && r.Status == statusPending && r.ApproveUrl != "" {
		fmt.Fprintln(a.Out, "\n  "+st.Info.Render("Share this with your operator to approve:"))
		fmt.Fprintln(a.Out, "  "+st.Command.Render(r.ApproveUrl))
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

func statusStyle(st theme.Styles, status string) string {
	switch status {
	case statusApproved:
		return st.Success.Render(status)
	case statusDenied, statusExpired, statusWithdrawn:
		return st.Warn.Render(status)
	default:
		return st.Accent.Render(status)
	}
}
