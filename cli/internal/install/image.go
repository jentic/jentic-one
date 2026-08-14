package install

import (
	"fmt"
	"io"
	"os"
	"strings"
)

const (
	// DefaultAppImageRepo is the published server image the Docker install path
	// pulls by default. It is the same image the release workflow builds and
	// signs (release.yml `publish-image`). Overridable via AppImageRepoEnv for
	// forks/mirrors, mirroring `make release-image REGISTRY=…`.
	DefaultAppImageRepo = "ghcr.io/jentic/jentic-one-app"

	// AppImageRepoEnv overrides the image repository (without a tag) for a fork
	// or an internal mirror, e.g. JENTIC_APP_IMAGE=my.registry/jentic-one-app.
	AppImageRepoEnv = "JENTIC_APP_IMAGE"

	// AppImageTagEnv pins the image tag (or a full @sha256: digest) to pull,
	// overriding the CLI-version → tag mapping. Mirrors the `--image-tag` flag.
	AppImageTagEnv = "JENTIC_APP_IMAGE_TAG"
)

// ResolveAppImage returns the fully-qualified app image reference the Docker
// install path pulls, following the pin ladder:
//
//	override (a full ref, a bare tag, or an @sha256: digest) — from --image-tag
//	         or $JENTIC_APP_IMAGE_TAG
//	CLI build version, when it is a real release (not "dev"/empty) → :X.Y.Z
//	"latest"
//
// The repository defaults to DefaultAppImageRepo and is overridable via
// $JENTIC_APP_IMAGE. An override that already looks like a full reference
// (contains a registry host, i.e. a "/" before any ":") is returned verbatim so
// an operator can pin `ghcr.io/…@sha256:…` or a mirror exactly.
func ResolveAppImage(version, override string) string {
	repo := strings.TrimSpace(os.Getenv(AppImageRepoEnv))
	if repo == "" {
		repo = DefaultAppImageRepo
	}

	// An explicit override wins. If it is already a full reference (has its own
	// registry/repo path), pass it through untouched; otherwise treat it as a
	// tag or @digest to apply to the resolved repo.
	if ov := strings.TrimSpace(firstNonEmpty(override, os.Getenv(AppImageTagEnv))); ov != "" {
		if isFullImageRef(ov) {
			return ov
		}
		return applyRef(repo, ov)
	}

	tag := "latest"
	if v := strings.TrimSpace(version); v != "" && v != "dev" {
		tag = strings.TrimPrefix(v, "v")
	}
	return repo + ":" + tag
}

// applyRef joins a repo with a bare tag or an @sha256: digest.
func applyRef(repo, ref string) string {
	if strings.HasPrefix(ref, "@") || strings.HasPrefix(ref, "sha256:") {
		digest := strings.TrimPrefix(ref, "@")
		return repo + "@" + digest
	}
	return repo + ":" + strings.TrimPrefix(ref, ":")
}

// isFullImageRef reports whether s already carries its own registry/repository
// (so it must not be re-prefixed with the default repo). We treat "a slash
// appears before any tag/digest separator" as "already a path". A bare tag
// ("v1.2.3") or "@sha256:…"/":tag" has no such slash.
func isFullImageRef(s string) bool {
	if strings.HasPrefix(s, "@") || strings.HasPrefix(s, ":") || strings.HasPrefix(s, "sha256:") {
		return false
	}
	// Cut at the first ':' or '@' (tag/digest) and look for a '/' in the name
	// part — a registry/repo always has one (ghcr.io/…, docker.io/…, my/repo).
	name := s
	if i := strings.IndexAny(s, ":@"); i >= 0 {
		name = s[:i]
	}
	return strings.Contains(name, "/")
}

// firstNonEmpty returns the first non-empty (after trim) string, or "".
func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

// AppImagePullError wraps a `docker pull` failure with the actionable hint that
// the GHCR package may be private (the first-release checklist gotcha).
type AppImagePullError struct {
	Ref string
	Err error
}

func (e *AppImagePullError) Error() string {
	return fmt.Sprintf("pull %s failed: %v", e.Ref, e.Err)
}

func (e *AppImagePullError) Unwrap() error { return e.Err }

// PullAppImage runs `docker pull <ref>`, streaming progress through w. On
// failure it returns an *AppImagePullError whose message carries the
// private-package hint (the GHCR package must be public, or a GITHUB_TOKEN /
// `docker login ghcr.io` is needed) — the first-release gotcha — so install
// fails fast with an actionable message instead of dying deep in `compose up`.
func PullAppImage(w io.Writer, ref string) error {
	out, err := runCapture(w, "", "pull", ref)
	if err == nil {
		return nil
	}
	lower := strings.ToLower(out)
	hint := ""
	switch {
	case strings.Contains(lower, "denied"), strings.Contains(lower, "unauthorized"),
		strings.Contains(lower, "not found"), strings.Contains(lower, "manifest unknown"):
		hint = "\n  The image may be private or not yet published. Ensure the GHCR package is public, " +
			"or run `docker login ghcr.io` (or set GITHUB_TOKEN) — or install from source with `--build-local`."
	}
	return &AppImagePullError{Ref: ref, Err: fmt.Errorf("%w%s", err, hint)}
}
