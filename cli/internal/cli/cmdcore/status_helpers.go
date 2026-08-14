package cmdcore

import (
	"fmt"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// IdentityOptions carries the --base-url override shared by the jenticctl
// install-facing commands (status/doctor): it points their server probes at a
// non-default control plane. Identity selection is NOT a flag anymore — the
// jentic tree acts on the active V2 context, and the ctl tree only reads the
// context read-only for display.
type IdentityOptions struct {
	BaseURL string
}

// Bind registers the --base-url flag onto cmd.
func (o *IdentityOptions) Bind(cmd *cobra.Command) {
	cmd.Flags().StringVar(&o.BaseURL, "base-url", "", "Jentic control-plane base URL")
}

// TokenStatus summarizes a cached V2 token as a human label plus a status dot.
func TokenStatus(t *auth.TokenSet) (label, dot string) {
	switch {
	case t == nil || t.AccessToken == "":
		return "none", DotWarn()
	case t.ExpiresAt.IsZero():
		return "valid", DotOK()
	case time.Now().After(t.ExpiresAt):
		return "expired", DotWarn()
	default:
		return fmt.Sprintf("valid (%s left)", time.Until(t.ExpiresAt).Round(time.Minute)), DotOK()
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
