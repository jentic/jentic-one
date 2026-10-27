package localagent

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// BanClass describes how a path is protected from being handed to the agent.
// The two classes are handled differently at the grant prompt (see
// localagentcmd/run.go): a SoftBan blocks only the path itself, a HardBan
// blocks its whole subtree.
type BanClass int

const (
	// NotBanned means the path is an ordinary, safe-to-grant location.
	NotBanned BanClass = iota
	// SoftBan means the path itself must not be granted because it holds
	// secrets directly (e.g. the operator's home, another user's home), but a
	// grant on a *subdirectory* below it is still allowed.
	SoftBan
	// HardBan means nothing anywhere in this subtree may be granted — the path
	// and every descendant is off-limits (e.g. ~/.ssh, ~/.jentic, ~/.aws,
	// keychains, browser profiles, and OS-owned system trees).
	HardBan
)

// DangerVerdict is the result of classifying a candidate grant path: its ban
// class and a human-readable reason (empty when NotBanned).
type DangerVerdict struct {
	Class  BanClass
	Reason string
}

// Banned reports whether the path may not be granted at all.
func (v DangerVerdict) Banned() bool { return v.Class != NotBanned }

// Classify decides how (if at all) granting the agent access to dir is
// restricted. It distinguishes two protection classes:
//
//   - HardBan: the path or any ancestor of it is a sensitive-subtree root
//     (dotfile credential dirs like ~/.ssh, ~/.jentic, ~/.aws; keychains;
//     browser profiles; the credential-bearing children of ~/.config; OS system
//     trees). NOTHING under these may be granted, so the check matches the path
//     itself AND any descendant.
//   - SoftBan: the path holds secrets directly and must not be granted as-is,
//     but a subdirectory beneath it still can be — a home root (the operator's
//     own or any other human's), or a config root like ~/.config whose whole
//     subtree would sweep in the HardBanned children if granted recursively.
//
// Both the candidate and operatorHome are canonicalized with Canonicalize
// (absolute, cleaned, symlinks resolved) before comparison, so a symlink whose
// real target sits inside a banned subtree can't dodge the ban — the ACL grant
// operates on that real target, so the ban must judge it too. Comparison is
// case-insensitive on case-insensitive filesystems (see pathEqual) so an alias
// like ~/.SSH is caught on a default macOS volume.
func Classify(dir, operatorHome string) DangerVerdict {
	abs := Canonicalize(dir)
	home := Canonicalize(operatorHome)

	// HardBan first: sensitive dotfile subtrees under the operator's home. The
	// whole subtree is off-limits, so match the root or any descendant. The root
	// is canonicalized too (a dotdir may itself be a symlink).
	if home != "" {
		for _, d := range sensitiveDotDirs {
			root := Canonicalize(filepath.Join(home, d))
			if pathEqual(abs, root) || pathHasPrefix(abs, root) {
				return DangerVerdict{
					HardBan,
					"this is inside a sensitive dir in the operator's home (" + d + ") holding keys/credentials",
				}
			}
		}
	}

	// HardBan: OS-owned system trees (root and everything below). Canonicalize
	// each root so a symlinked system path (e.g. /etc → /private/etc on macOS,
	// which is where a resolved candidate lands) still matches.
	for _, sys := range systemTrees {
		root := Canonicalize(sys)
		if pathEqual(abs, root) || pathHasPrefix(abs, root) {
			return DangerVerdict{HardBan, "this is inside a system directory (" + sys + ")"}
		}
	}

	// SoftBan: the operator's own home root — holds secrets directly, but a
	// subdirectory below it may still be granted.
	if home != "" && pathEqual(abs, home) {
		return DangerVerdict{
			SoftBan,
			"this is the operator's home — granting here re-opens the credential boundary (keys, browser profile, SSH)",
		}
	}

	// SoftBan: config roots (e.g. ~/.config) that hold credential subdirectories
	// directly. Granting the whole dir recursively would sweep in the HardBanned
	// children (~/.config/gcloud, ~/.config/gh, browser profiles), so the root
	// itself is off-limits — but a specific safe child (~/.config/nvim) still can
	// be granted.
	if home != "" {
		for _, d := range softBanConfigDirs {
			if pathEqual(abs, Canonicalize(filepath.Join(home, d))) {
				return DangerVerdict{
					SoftBan,
					"this holds credential subdirectories (" + d + ") — grant a specific safe subdirectory instead",
				}
			}
		}
	}

	// SoftBan: any other human's home root (/Users/<name> or /home/<name>
	// exactly). The agent's own home lives under /Users/Shared or /opt, which is
	// not a direct child here.
	for _, base := range []string{"/Users", "/home"} {
		if isDirectChild(abs, base) {
			return DangerVerdict{SoftBan, "this is another user's home directory"}
		}
	}

	return DangerVerdict{NotBanned, ""}
}

// Canonicalize resolves p to the absolute, cleaned, symlink-free path the OS will
// actually act on: it is the single canonical form used for classification,
// granting, recording, and revoking so those steps can never disagree about which
// directory a path names. A path that no longer exists (EvalSymlinks fails) falls
// back to the cleaned absolute form. An empty input returns empty.
func Canonicalize(p string) string {
	if p == "" {
		return ""
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		abs = p
	}
	abs = filepath.Clean(abs)
	if resolved, err := filepath.EvalSymlinks(abs); err == nil {
		return resolved
	}
	return abs
}

// caseInsensitiveFS reports whether path comparisons must ignore case. macOS's
// default APFS/HFS+ volume is case-insensitive, so ~/.SSH and ~/.ssh name the same
// directory; a case-sensitive ban check would miss the alias. We fold-compare on
// darwin — over-banning a genuinely case-sensitive volume there is the safe
// direction for a security gate.
var caseInsensitiveFS = runtime.GOOS == "darwin"

// pathEqual reports whether two cleaned paths name the same location, honouring
// case-insensitive filesystems.
func pathEqual(a, b string) bool {
	if caseInsensitiveFS {
		return strings.EqualFold(a, b)
	}
	return a == b
}

// pathHasPrefix reports whether p is a strict descendant of the directory prefix
// (segment-wise, so /a/bc is not treated as under /a/b), honouring
// case-insensitive filesystems.
func pathHasPrefix(p, prefix string) bool {
	withSep := prefix + string(filepath.Separator)
	if len(p) < len(withSep) {
		return false
	}
	if caseInsensitiveFS {
		return strings.EqualFold(p[:len(withSep)], withSep)
	}
	return strings.HasPrefix(p, withSep)
}

// DangerReason returns the human reason a path is restricted (empty when it is
// freely grantable). It is a thin wrapper over Classify for display-only callers
// that don't need the ban class.
func DangerReason(dir, operatorHome string) string {
	return Classify(dir, operatorHome).Reason
}

// sensitiveDotDirs are the directories under a home whose entire subtree must
// never be handed to the agent (HardBan). ~/.config is deliberately NOT here — it
// is a shared XDG root holding both credentials and innocuous app state, so a
// blanket subtree ban would wrongly block ~/.config/nvim. Instead its
// credential-bearing children are named explicitly below, and ~/.config itself is
// a SoftBan (see softBanConfigDirs) so it can't be granted whole.
var sensitiveDotDirs = []string{
	".ssh", ".jentic", ".aws", ".gnupg", ".gcloud", ".kube", ".docker",
	"Library/Keychains", ".mozilla",
	"Library/Application Support/Google/Chrome",
	// Credential-bearing children of the shared ~/.config XDG root.
	".config/gcloud", ".config/google-chrome", ".config/chromium",
	".config/gh", ".config/jentic",
}

// softBanConfigDirs are config roots that hold HardBanned credential children
// directly. The root itself must not be granted (a recursive grant would sweep in
// those children), but a specific safe child under it still can be — hence SoftBan,
// not HardBan. Cf. sensitiveDotDirs, which names the sensitive children.
var softBanConfigDirs = []string{".config"}

// systemTrees are OS-owned roots whose entire subtree must never be
// agent-granted (HardBan).
var systemTrees = []string{"/etc", "/usr", "/var", "/System", "/Library", "/bin", "/sbin", "/"}

// isDirectChild reports whether abs is exactly base/<one-segment> (a home root
// like /Users/alice), not base itself and not a deeper descendant.
func isDirectChild(abs, base string) bool {
	if !strings.HasPrefix(abs, base+string(filepath.Separator)) {
		return false
	}
	rest := strings.TrimPrefix(abs, base+string(filepath.Separator))
	return rest != "" && !strings.Contains(rest, string(filepath.Separator))
}

// OperatorHome returns the operator's home directory (os.UserHomeDir), or "".
func OperatorHome() string {
	h, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return h
}
