package cmd

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/apiclient"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newApisCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
	cmd := &cobra.Command{
		Use:     "apis",
		Aliases: []string{"api"},
		Short: "Browse and manage APIs in the local registry",
		Long: "apis inspects and manages the APIs imported into this deployment's local\n" +
			"registry — the other half of `jentic catalog import`. Run bare on a\n" +
			"terminal to open an interactive browser (list, preview operations, manage\n" +
			"revisions); the subcommands (list/show/revisions/operations/inspect/\n" +
			"promote/archive/rm/spec) are script-friendly.\n" +
			"Requires a registered agent (run `jentic register` first).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.apisBrowse(cmd.Context(), ident)
		},
	}
	cmd.PersistentFlags().StringVar(&ident.profile, "profile", "", "profile name (default: config default_profile)")
	cmd.PersistentFlags().StringVar(&ident.baseURL, "base-url", "", "Jentic control-plane base URL")

	cmd.AddCommand(newApisListCmd(app, ident))
	cmd.AddCommand(newApisShowCmd(app, ident))
	cmd.AddCommand(newApisRevisionsCmd(app, ident))
	cmd.AddCommand(newApisOperationsCmd(app, ident))
	cmd.AddCommand(newApisInspectCmd(app, ident))
	cmd.AddCommand(newApisPromoteCmd(app, ident))
	cmd.AddCommand(newApisArchiveCmd(app, ident))
	cmd.AddCommand(newApisRmCmd(app, ident))
	cmd.AddCommand(newApisSpecCmd(app, ident))
	return cmd
}

// ── command constructors ─────────────────────────────────────────────────────

func newApisListCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &apisListOptions{}
	cmd := &cobra.Command{
		Use:     "list",
		Aliases: []string{"ls"},
		Short:   "List locally registered APIs",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.apisList(cmd.Context(), ident, o)
		},
	}
	cmd.Flags().StringVar(&o.vendor, "vendor", "", "only APIs from this vendor")
	cmd.Flags().IntVar(&o.limit, "limit", 50, "page size (1-200)")
	cmd.Flags().BoolVar(&o.all, "all", false, "follow pagination and list every matching API")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newApisShowCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &apisShowOptions{}
	cmd := &cobra.Command{
		Use:   "show <vendor/name/version>",
		Short: "Show an API and preview its operations",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.apisShow(cmd.Context(), ident, o, args[0])
		},
	}
	cmd.Flags().IntVar(&o.limit, "limit", 0, "max operations to preview (default 50)")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newApisRevisionsCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &apisRevisionsOptions{}
	cmd := &cobra.Command{
		Use:     "revisions <vendor/name/version>",
		Aliases: []string{"revs"},
		Short:   "List the revisions of an API",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.apisRevisions(cmd.Context(), ident, o, args[0])
		},
	}
	cmd.Flags().StringSliceVar(&o.states, "state", nil, "filter by state (draft, published, archived); repeatable")
	cmd.Flags().IntVar(&o.limit, "limit", 50, "page size (1-200)")
	cmd.Flags().BoolVar(&o.all, "all", false, "follow pagination and list every revision")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newApisOperationsCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &apisOperationsOptions{}
	cmd := &cobra.Command{
		Use:     "operations <vendor/name/version>",
		Aliases: []string{"ops"},
		Short:   "List the operations of an API (current revision by default)",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.apisOperations(cmd.Context(), ident, o, args[0])
		},
	}
	cmd.Flags().StringVar(&o.revision, "revision", "", "list operations for a specific revision id")
	cmd.Flags().IntVar(&o.limit, "limit", 50, "page size (1-200)")
	cmd.Flags().BoolVar(&o.all, "all", false, "follow pagination and list every operation")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newApisInspectCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &apisInspectOptions{}
	cmd := &cobra.Command{
		Use:   "inspect <operation_id>",
		Short: "Inspect an operation's structural detail",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.apisInspect(cmd.Context(), ident, o, args[0])
		},
	}
	cmd.Flags().StringVar(&o.revision, "revision", "", "pin to a specific revision id")
	cmd.Flags().StringVar(&o.format, "format", "markdown", "output format: markdown, json, or openapi")
	return cmd
}

func newApisPromoteCmd(app *App, ident *identityOptions) *cobra.Command {
	return &cobra.Command{
		Use:   "promote <vendor/name/version> <revision_id>",
		Short: "Promote a draft revision to live",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.apisLifecycle(cmd.Context(), ident, args[0], args[1], lifecyclePromote)
		},
	}
}

func newApisArchiveCmd(app *App, ident *identityOptions) *cobra.Command {
	return &cobra.Command{
		Use:   "archive <vendor/name/version> <revision_id>",
		Short: "Archive a draft revision",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.apisLifecycle(cmd.Context(), ident, args[0], args[1], lifecycleArchive)
		},
	}
}

func newApisRmCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &apisRmOptions{}
	cmd := &cobra.Command{
		Use:     "rm <vendor/name/version> [revision_id]",
		Aliases: []string{"delete"},
		Short:   "Delete an API (or an archived revision)",
		Args:    cobra.RangeArgs(1, 2),
		RunE: func(cmd *cobra.Command, args []string) error {
			revisionID := ""
			if len(args) == 2 {
				revisionID = args[1]
			}
			return app.apisRemove(cmd.Context(), ident, o, args[0], revisionID)
		},
	}
	cmd.Flags().BoolVar(&o.yes, "yes", false, "skip the confirmation prompt")
	return cmd
}

func newApisSpecCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &apisSpecOptions{}
	cmd := &cobra.Command{
		Use:   "spec <vendor/name/version>",
		Short: "Download the OpenAPI spec for an API",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.apisSpec(cmd.Context(), ident, o, args[0])
		},
	}
	cmd.Flags().StringVar(&o.revision, "revision", "", "download a specific revision id (default: current/live)")
	cmd.Flags().BoolVar(&o.yaml, "yaml", false, "request YAML instead of JSON")
	cmd.Flags().StringVar(&o.out, "out", "", "write to this file instead of stdout")
	return cmd
}

// ── option structs ───────────────────────────────────────────────────────────

type apisListOptions struct {
	vendor string
	limit  int
	all    bool
	json   bool
}

type apisShowOptions struct {
	limit int
	json  bool
}

type apisRevisionsOptions struct {
	states []string
	limit  int
	all    bool
	json   bool
}

type apisOperationsOptions struct {
	revision string
	limit    int
	all      bool
	json     bool
}

type apisInspectOptions struct {
	revision string
	format   string
}

type apisRmOptions struct {
	yes bool
}

type apisSpecOptions struct {
	revision string
	yaml     bool
	out      string
}

type lifecycleAction int

const (
	lifecyclePromote lifecycleAction = iota
	lifecycleArchive
)

// ── auth ─────────────────────────────────────────────────────────────────────

// apisSession resolves the active profile's agent token and returns an apis
// client bound to the control-plane base URL.
func (a *App) apisSession(ctx context.Context, ident *identityOptions) (*apiclient.Client, string, error) {
	baseURL, token, err := a.agentSession(ctx, ident)
	if err != nil {
		return nil, "", err
	}
	return apiclient.New(baseURL), token, nil
}

// ── browse (bare) ────────────────────────────────────────────────────────────

func (a *App) apisBrowse(ctx context.Context, ident *identityOptions) error {
	if !term.IsTerminal(os.Stdin.Fd()) {
		return a.apisList(ctx, ident, &apisListOptions{limit: 50})
	}
	return a.runApisBrowser(ctx, ident)
}

// ── list ─────────────────────────────────────────────────────────────────────

func (a *App) apisList(ctx context.Context, ident *identityOptions, o *apisListOptions) error {
	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	limit := o.limit
	if limit <= 0 {
		limit = 50
	}
	params := apiclient.ListParams{Vendor: o.vendor, Limit: limit}

	var apis []apiclient.API
	for {
		page, err := client.List(ctx, token, params)
		if err != nil {
			return apisListErr(err)
		}
		apis = append(apis, page.Data...)
		if !o.all || !page.HasMore || page.NextCursor == "" {
			break
		}
		params.Cursor = page.NextCursor
	}

	if o.json {
		return writeJSON(a.Out, map[string]any{"data": apis})
	}
	a.printAPIList(apis)
	return nil
}

func (a *App) printAPIList(apis []apiclient.API) {
	fmt.Fprintln(a.Out, theme.Heading.Render("APIs"))
	if len(apis) == 0 {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Dim.Render("no APIs imported yet — try `jentic catalog`"))
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
func apiRow(api apiclient.API) string {
	dot := dotDown()
	if api.CurrentRevisionID != "" {
		dot = dotOK()
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

// ── show ─────────────────────────────────────────────────────────────────────

func (a *App) apisShow(ctx context.Context, ident *identityOptions, o *apisShowOptions, ref string) error {
	vendor, name, version, err := parseAPIRef(ref)
	if err != nil {
		return err
	}
	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	api, err := client.Get(ctx, token, vendor, name, version)
	if err != nil {
		return apiNotFoundErr(err, ref)
	}
	limit := o.limit
	if limit <= 0 {
		limit = 50
	}
	ops, operr := client.Operations(ctx, token, vendor, name, version, "", "", limit)

	if o.json {
		out := map[string]any{"api": api}
		if operr == nil {
			out["operations"] = ops.Data
		}
		return writeJSON(a.Out, out)
	}

	a.printAPIDetail(api)
	if operr != nil {
		fmt.Fprintln(a.Out, dotWarn()+" "+theme.Warnf("operations unavailable: %v", operr))
		return nil
	}
	a.printOperations(ops.Data, ops.HasMore)
	return nil
}

func (a *App) printAPIDetail(api *apiclient.API) {
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
	dot := dotDown()
	if api.CurrentRevisionID != "" {
		state, dot = "live: "+api.CurrentRevisionID, dotOK()
	}
	fmt.Fprintln(a.Out, "  "+dot+" "+theme.Field("current", state))
	fmt.Fprintln(a.Out, "  "+theme.Field("revisions", strconv.Itoa(api.RevisionCount)))
	fmt.Fprintln(a.Out, "  "+theme.Field("operations", strconv.Itoa(api.OperationCount)))
	if len(api.SecuritySchemes) > 0 {
		fmt.Fprintln(a.Out, "  "+theme.Field("auth", strings.Join(api.SecuritySchemes, ", ")))
	}
}

func (a *App) printOperations(ops []apiclient.Operation, hasMore bool) {
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
func apiOpLine(op apiclient.Operation) string {
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

// ── revisions ────────────────────────────────────────────────────────────────

func (a *App) apisRevisions(ctx context.Context, ident *identityOptions, o *apisRevisionsOptions, ref string) error {
	vendor, name, version, err := parseAPIRef(ref)
	if err != nil {
		return err
	}
	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	limit := o.limit
	if limit <= 0 {
		limit = 50
	}
	params := apiclient.RevisionParams{States: o.states, Limit: limit}

	var revs []apiclient.Revision
	for {
		page, err := client.Revisions(ctx, token, vendor, name, version, params)
		if err != nil {
			return apiNotFoundErr(err, ref)
		}
		revs = append(revs, page.Data...)
		if !o.all || !page.HasMore || page.NextCursor == "" {
			break
		}
		params.Cursor = page.NextCursor
	}

	if o.json {
		return writeJSON(a.Out, map[string]any{"data": revs})
	}
	a.printRevisions(ref, revs)
	return nil
}

func (a *App) printRevisions(ref string, revs []apiclient.Revision) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Revisions")+theme.Dim.Render("  "+ref))
	if len(revs) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no revisions"))
		return
	}
	for _, rev := range revs {
		fmt.Fprintln(a.Out, "  "+revisionLine(rev))
	}
}

func revisionLine(rev apiclient.Revision) string {
	dot := dotDown()
	switch {
	case rev.IsCurrent:
		dot = dotOK()
	case rev.State == "draft":
		dot = dotWarn()
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

// ── operations ───────────────────────────────────────────────────────────────

func (a *App) apisOperations(ctx context.Context, ident *identityOptions, o *apisOperationsOptions, ref string) error {
	vendor, name, version, err := parseAPIRef(ref)
	if err != nil {
		return err
	}
	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	limit := o.limit
	if limit <= 0 {
		limit = 50
	}

	var ops []apiclient.Operation
	cursor := ""
	var hasMore bool
	for {
		page, err := client.Operations(ctx, token, vendor, name, version, o.revision, cursor, limit)
		if err != nil {
			return apiNotFoundErr(err, ref)
		}
		ops = append(ops, page.Data...)
		hasMore = page.HasMore
		if !o.all || !page.HasMore || page.NextCursor == "" {
			break
		}
		cursor = page.NextCursor
	}

	if o.json {
		return writeJSON(a.Out, map[string]any{"data": ops})
	}
	a.printOperations(ops, hasMore && !o.all)
	return nil
}

// ── inspect ──────────────────────────────────────────────────────────────────

func (a *App) apisInspect(ctx context.Context, ident *identityOptions, o *apisInspectOptions, operationID string) error {
	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	body, err := client.Inspect(ctx, token, operationID, o.revision, o.format)
	if err != nil {
		var he *apiclient.HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			return fmt.Errorf("operation %q not found", operationID)
		}
		return err
	}
	out := strings.TrimRight(string(body), "\n")
	fmt.Fprintln(a.Out, out)
	return nil
}

// ── lifecycle (promote / archive) ────────────────────────────────────────────

func (a *App) apisLifecycle(ctx context.Context, ident *identityOptions, ref, revisionID string, action lifecycleAction) error {
	vendor, name, version, err := parseAPIRef(ref)
	if err != nil {
		return err
	}
	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	switch action {
	case lifecyclePromote:
		if err := client.Promote(ctx, token, vendor, name, version, revisionID); err != nil {
			return apiActionErr(err, ref)
		}
		fmt.Fprintln(a.Out, theme.Successf("Promoted %s revision %s to live", ref, revisionID))
	case lifecycleArchive:
		if err := client.Archive(ctx, token, vendor, name, version, revisionID); err != nil {
			return apiActionErr(err, ref)
		}
		fmt.Fprintln(a.Out, theme.Successf("Archived %s revision %s", ref, revisionID))
	}
	return nil
}

// ── rm (delete) ──────────────────────────────────────────────────────────────

func (a *App) apisRemove(ctx context.Context, ident *identityOptions, o *apisRmOptions, ref, revisionID string) error {
	vendor, name, version, err := parseAPIRef(ref)
	if err != nil {
		return err
	}

	target := fmt.Sprintf("API %s and all its revisions", ref)
	if revisionID != "" {
		target = fmt.Sprintf("archived revision %s of %s", revisionID, ref)
	}
	if !o.yes {
		if !term.IsTerminal(os.Stdin.Fd()) {
			return fmt.Errorf("refusing to delete %s without confirmation; pass --yes", target)
		}
		ok, err := confirmDelete(target)
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
	}

	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	if revisionID != "" {
		if err := client.DeleteRevision(ctx, token, vendor, name, version, revisionID); err != nil {
			return apiActionErr(err, ref)
		}
		fmt.Fprintln(a.Out, theme.Successf("Deleted revision %s of %s", revisionID, ref))
		return nil
	}
	if err := client.DeleteAPI(ctx, token, vendor, name, version); err != nil {
		return apiNotFoundErr(err, ref)
	}
	fmt.Fprintln(a.Out, theme.Successf("Deleted API %s", ref))
	return nil
}

func confirmDelete(target string) (bool, error) {
	confirm := false
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title(fmt.Sprintf("Delete %s? This cannot be undone.", target)).
			Affirmative("Yes, delete").
			Negative("Cancel").
			Value(&confirm),
	); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return confirm, nil
}

// ── spec ─────────────────────────────────────────────────────────────────────

func (a *App) apisSpec(ctx context.Context, ident *identityOptions, o *apisSpecOptions, ref string) error {
	vendor, name, version, err := parseAPIRef(ref)
	if err != nil {
		return err
	}
	client, token, err := a.apisSession(ctx, ident)
	if err != nil {
		return err
	}
	body, err := client.Spec(ctx, token, vendor, name, version, o.revision, o.yaml)
	if err != nil {
		return apiNotFoundErr(err, ref)
	}
	if o.out != "" {
		if err := os.WriteFile(o.out, body, 0o600); err != nil {
			return fmt.Errorf("write %s: %w", o.out, err)
		}
		fmt.Fprintln(a.Out, theme.Successf("Wrote %s spec to %s", ref, o.out))
		return nil
	}
	_, err = a.Out.Write(body)
	if err == nil && len(body) > 0 && body[len(body)-1] != '\n' {
		fmt.Fprintln(a.Out)
	}
	return err
}

// ── helpers ──────────────────────────────────────────────────────────────────

// parseAPIRef splits a "vendor/name/version" identity into its three parts.
// Local API identities never contain slashes within a segment, so an exact
// three-part split is unambiguous.
func parseAPIRef(ref string) (vendor, name, version string, err error) {
	parts := strings.Split(ref, "/")
	if len(parts) != 3 || parts[0] == "" || parts[1] == "" || parts[2] == "" {
		return "", "", "", fmt.Errorf("invalid API reference %q; expected vendor/name/version", ref)
	}
	return parts[0], parts[1], parts[2], nil
}

func apiRefLabel(ref apiclient.APIRef) string {
	return ref.Vendor + "/" + ref.Name + "/" + ref.Version
}

// apisListErr maps a missing route to a friendly "not available" message.
func apisListErr(err error) error {
	var he *apiclient.HTTPError
	if errors.As(err, &he) && (he.StatusCode == http.StatusNotFound || he.StatusCode == http.StatusNotImplemented) {
		return fmt.Errorf("registry not available on this server (HTTP %d)", he.StatusCode)
	}
	return err
}

// apiNotFoundErr maps a 404 to a clear "API not found" message.
func apiNotFoundErr(err error, ref string) error {
	var he *apiclient.HTTPError
	if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
		return fmt.Errorf("API %q not found in the local registry", ref)
	}
	return err
}

// apiActionErr maps a 403 to an org-permission hint, otherwise passes through.
func apiActionErr(err error, ref string) error {
	var he *apiclient.HTTPError
	if errors.As(err, &he) {
		switch he.StatusCode {
		case http.StatusForbidden:
			return fmt.Errorf("not permitted: %s", he.Detail())
		case http.StatusNotFound:
			return fmt.Errorf("API %q or revision not found", ref)
		}
	}
	return err
}
