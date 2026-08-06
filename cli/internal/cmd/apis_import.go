package cmd

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newApisImportCmd is `jentic apis import <file|url>` (impl/5.0 §6b,
// jentic-one#777): submit an OpenAPI document to the catalog via POST /apis. A
// URL is sent as a fetchable source; a local file is read and sent inline. Import
// is asynchronous — the command reports the returned job id/status.
//
// This subcommand is the V2-SDK path; the sibling `apis` subcommands remain on
// the shipped internal/apiclient path until the broader re-plumb.
func newApisImportCmd(_ *App) *cobra.Command {
	var vendor, name, version string
	cmd := &cobra.Command{
		Use:   "import <file|url>",
		Short: "Import an OpenAPI document into the catalog",
		Long: "import submits an OpenAPI spec to the catalog. The argument is either a\n" +
			"local file path (read and sent inline) or an http(s) URL (sent as a\n" +
			"fetchable source). Import is asynchronous; the command prints the job id.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}

			source, serr := buildImportSource(args[0], vendor, name, version)
			if serr != nil {
				return reportCoded(aud, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: serr.Error()})
			}

			body := control.ApiImportRequest{Sources: []control.ApiImportRequest_Sources_Item{source}}
			resp, err := client.ImportApisWithResponse(cmd.Context(), body)
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			if resp.JSON202 == nil {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeInternalError,
					Msg:  fmt.Sprintf("import failed (status %d)", resp.StatusCode()),
				})
			}
			aud.Render(ux.Result{
				Status:   ux.StatusCreated,
				Resource: "api-import",
				Message:  "Import accepted; the catalog is processing it asynchronously.",
				Fields:   map[string]any{"response": *resp.JSON202},
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&vendor, "vendor", "", "Override the vendor for the imported API")
	cmd.Flags().StringVar(&name, "name", "", "Override the API name")
	cmd.Flags().StringVar(&version, "version", "", "Override the API version")
	return cmd
}

// buildImportSource turns a file-or-URL argument into a source union item. An
// argument parseable as an http(s) URL becomes an ApiSourceUrl; anything else is
// treated as a local file read inline.
func buildImportSource(arg, vendor, name, version string) (control.ApiImportRequest_Sources_Item, error) {
	var item control.ApiImportRequest_Sources_Item
	optPtr := func(s string) *string {
		if s == "" {
			return nil
		}
		return &s
	}

	if u, err := url.Parse(arg); err == nil && (u.Scheme == "http" || u.Scheme == "https") {
		src := control.ApiSourceUrl{
			Type:    control.Url,
			Url:     arg,
			Vendor:  optPtr(vendor),
			ApiName: optPtr(name),
			Version: optPtr(version),
		}
		if merr := item.FromApiSourceUrl(src); merr != nil {
			return item, merr
		}
		return item, nil
	}

	data, err := os.ReadFile(arg) //nolint:gosec // operator-supplied path; same trust as any file arg.
	if err != nil {
		return item, fmt.Errorf("reading %s: %w", arg, err)
	}
	src := control.ApiSourceInline{
		Type:     control.Inline,
		Filename: filepath.Base(arg),
		Content:  string(data),
		Vendor:   optPtr(vendor),
		ApiName:  optPtr(name),
		Version:  optPtr(version),
	}
	if merr := item.FromApiSourceInline(src); merr != nil {
		return item, merr
	}
	return item, nil
}
