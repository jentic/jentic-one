package cmdcore

import (
	"fmt"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// IdentityOptions carries the common --profile/--base-url selection flags shared
// by the identity-scoped commands in both trees (jentic: status/logout/catalog/
// apis/…; jenticctl: status/doctor). Fields are exported so the tree packages
// (which alias this type as identityOptions) can read them from their own
// command handlers.
type IdentityOptions struct {
	Profile string
	BaseURL string
}

// Bind registers the --profile and --base-url flags onto cmd.
func (o *IdentityOptions) Bind(cmd *cobra.Command) {
	cmd.Flags().StringVar(&o.Profile, "profile", "", "profile name (default: config default_profile)")
	cmd.Flags().StringVar(&o.BaseURL, "base-url", "", "Jentic control-plane base URL")
}

// TokenStatus summarizes a cached token pair as a human label plus a status dot.
func TokenStatus(t *profile.Tokens) (label, dot string) {
	switch {
	case t == nil || t.AccessToken == "":
		return "none", DotWarn()
	case t.Expired(0):
		return "expired", DotWarn()
	case t.AccessExpiresAt.IsZero():
		return "valid", DotOK()
	default:
		return fmt.Sprintf("valid (%s left)", time.Until(t.AccessExpiresAt).Round(time.Minute)), DotOK()
	}
}

// IdentityLabel picks the most descriptive field from a /me response.
func IdentityLabel(me map[string]any) string {
	for _, k := range []string{"name", "email", "sub", "client_id", "id"} {
		if s, ok := me[k].(string); ok && s != "" {
			return s
		}
	}
	return "ok"
}

// ValueOr returns v, or fallback when v is empty/whitespace.
func ValueOr(v, fallback string) string {
	if strings.TrimSpace(v) == "" {
		return fallback
	}
	return v
}

// DotOK is the status glyph for a present/healthy item (filled).
func DotOK() string { return theme.Success.Render("●") }

// DotWarn is the status glyph for a degraded/warning item (filled, amber).
func DotWarn() string { return theme.Warn.Render("●") }

// DotDown is the status glyph for an absent/offline item (hollow).
func DotDown() string { return theme.Dim.Render("○") }

// DotFail is the status glyph for a failed item.
func DotFail() string { return theme.Error.Render("✗") }
