package update

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
)

// releaseTag matches a canonical release tag: a `v`-prefixed three-part semver
// with no pre-release/build suffix (e.g. "v0.15.3"). This deliberately excludes
// noise tags such as "cli/v0.15.0", pre-releases like "v1.0.0-rc1", and the
// "^{}" peeled-tag lines that `git ls-remote --tags` also prints.
var releaseTag = regexp.MustCompile(`^v(\d+)\.(\d+)\.(\d+)$`)

// semver is a parsed three-part version used for ordering release tags. The
// CLI intentionally ships no third-party semver dependency, so this covers just
// the major.minor.patch shape the release tags use.
type semver struct {
	major, minor, patch int
}

// parseSemver parses a version string of the form "[v]MAJOR.MINOR.PATCH" into a
// semver. A leading "v" is optional. Anything that is not a clean three-part
// numeric version (a branch name, "dev", a SHA, or a pre-release) fails to
// parse and callers treat that as "unknown".
func parseSemver(s string) (semver, bool) {
	s = strings.TrimSpace(s)
	s = strings.TrimPrefix(s, "v")
	parts := strings.Split(s, ".")
	if len(parts) != 3 {
		return semver{}, false
	}
	nums := make([]int, 3)
	for i, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 {
			return semver{}, false
		}
		nums[i] = n
	}
	return semver{major: nums[0], minor: nums[1], patch: nums[2]}, true
}

// compareSemver returns -1, 0, or 1 as a is less than, equal to, or greater
// than b.
func compareSemver(a, b semver) int {
	switch {
	case a.major != b.major:
		return cmpInt(a.major, b.major)
	case a.minor != b.minor:
		return cmpInt(a.minor, b.minor)
	default:
		return cmpInt(a.patch, b.patch)
	}
}

func cmpInt(a, b int) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	default:
		return 0
	}
}

// NewerAvailable reports whether latest is a newer release than installed.
//
// When installed does not parse as a clean semver — e.g. it is "dev", a branch
// name, or a SHA (an unreleased/source build) — we cannot meaningfully compare,
// so we conservatively report true so such builds are offered the latest
// release. When latest itself does not parse we report false: there is nothing
// sensible to update to.
func NewerAvailable(installed, latest string) bool {
	lv, ok := parseSemver(latest)
	if !ok {
		return false
	}
	iv, ok := parseSemver(installed)
	if !ok {
		return true
	}
	return compareSemver(lv, iv) > 0
}

// LatestReleaseTag resolves the highest canonical release tag (vMAJOR.MINOR.PATCH)
// in repo via `git ls-remote --tags`, returning it with its `v` prefix (e.g.
// "v0.15.3"). A token (GitHub PAT) authenticates against private repositories,
// mirroring lsRemote. It errors when git is unavailable or no release tag exists.
func LatestReleaseTag(ctx context.Context, repo, token string) (string, error) {
	if repo == "" {
		return "", errors.New("no repository to check")
	}
	if _, err := exec.LookPath("git"); err != nil {
		return "", errors.New("`git` is required to check for updates but was not found on PATH")
	}

	url := "https://github.com/" + repo + ".git"
	args := append([]string{"-c", "credential.helper="}, authArgs(token)...)
	args = append(args, "ls-remote", "--tags", url, "v*")

	cmd := exec.CommandContext(ctx, "git", args...) //nolint:gosec // args are CLI-internal; repo comes from the manifest/flags.
	// Never fall back to an interactive prompt: a missing/invalid token must
	// fail fast rather than block waiting for a username on the terminal.
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=true", "GCM_INTERACTIVE=never")
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("git ls-remote --tags failed (for a private repo, set GITHUB_TOKEN): %w", err)
	}

	tag, ok := highestReleaseTag(string(out))
	if !ok {
		return "", fmt.Errorf("no release tags found in %s", repo)
	}
	return tag, nil
}

// highestReleaseTag scans `git ls-remote --tags` output and returns the highest
// canonical release tag (with its `v` prefix). Lines have the form
// "<sha>\trefs/tags/<name>"; non-release and peeled ("^{}") tags are ignored.
func highestReleaseTag(out string) (string, bool) {
	var best semver
	var bestTag string
	found := false
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(strings.TrimSpace(line))
		if len(fields) < 2 {
			continue
		}
		name := strings.TrimPrefix(fields[1], "refs/tags/")
		if !releaseTag.MatchString(name) {
			continue
		}
		v, ok := parseSemver(name)
		if !ok {
			continue
		}
		if !found || compareSemver(v, best) > 0 {
			best = v
			bestTag = name
			found = true
		}
	}
	return bestTag, found
}
