package egressproxy

import (
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

// gitHost is the one governed host whose git smart-HTTP endpoints are
// relayed: the broker's governed git pipeline serves github.com only.
const gitHost = "github.com"

// gitServices are the smart-HTTP services the broker's git pipeline serves.
var gitServices = map[string]bool{"git-upload-pack": true, "git-receive-pack": true}

// gitRoute is how the proxy treats a request on a governed host.
type gitRoute int

const (
	// notGit is any request that is not git and not on gitHost: relayed to
	// the broker's proxy route like every other governed call.
	notGit gitRoute = iota
	// gitRelay is git smart-HTTP to github.com: relayed to the broker, which
	// runs it through its governed git pipeline.
	gitRelay
	// gitRefuse is a git request the broker does not serve (dumb HTTP,
	// git-upload-archive, an endpoint on another host, an encoded path).
	gitRefuse
	// webRefuse is anything else on github.com (web pages, the OAuth
	// endpoints, Git LFS): nothing Jentic governs lives there.
	webRefuse
)

// classifyGit decides a request on governed host host. Smart HTTP is
// `GET /<owner>/<repo>[.git]/info/refs?service=<service>` and
// `POST /<owner>/<repo>[.git]/<service>` for git-upload-pack and
// git-receive-pack, with the path exactly as sent (no percent-encoding).
func classifyGit(host, method string, u *url.URL) gitRoute {
	looksGit := isGitPath(u.Path)
	if host != gitHost {
		if looksGit {
			return gitRefuse
		}
		return notGit
	}
	if !looksGit {
		return webRefuse
	}
	if u.RawPath != "" {
		return gitRefuse
	}
	segments := strings.Split(u.Path, "/")
	if len(segments) < 4 || segments[0] != "" || !validRepoName(segments[1]) || !validRepoName(segments[2]) ||
		strings.EqualFold(segments[2], ".git") {
		return gitRefuse
	}
	rest := segments[3:]
	switch {
	case len(rest) == 2 && rest[0] == "info" && rest[1] == "refs" && method == http.MethodGet:
		services := u.Query()["service"]
		if len(services) == 1 && gitServices[services[0]] {
			return gitRelay
		}
	case len(rest) == 1 && gitServices[rest[0]] && method == http.MethodPost && u.RawQuery == "":
		return gitRelay
	}
	return gitRefuse
}

// isGitPath reports whether a path is a git HTTP endpoint: ref discovery
// (`…/info/refs`), a pack RPC (`…/git-upload-pack`, `…/git-receive-pack`,
// `…/git-upload-archive`) or a dumb-HTTP file (`…/HEAD`, `…/objects/…`) of
// a `.git` repository.
func isGitPath(path string) bool {
	switch {
	case strings.HasSuffix(path, "/info/refs"),
		strings.HasSuffix(path, "/git-upload-pack"),
		strings.HasSuffix(path, "/git-receive-pack"),
		strings.HasSuffix(path, "/git-upload-archive"):
		return true
	}
	if i := strings.Index(path, ".git/"); i > 0 {
		rest := path[i+len(".git/"):]
		return rest == "HEAD" || strings.HasPrefix(rest, "objects/") || strings.HasPrefix(rest, "info/")
	}
	return false
}

// validRepoName reports whether s can be a GitHub owner or repository name
// (letters, digits, '.', '-', '_'; never "." or "..").
func validRepoName(s string) bool {
	if s == "" || s == "." || s == ".." || len(s) > 100 {
		return false
	}
	for _, r := range s {
		if (r < 'a' || r > 'z') && (r < 'A' || r > 'Z') && (r < '0' || r > '9') && r != '.' && r != '-' && r != '_' {
			return false
		}
	}
	return true
}

// gitRefusedMessage is what git prints (as "remote: …" lines) for a git
// request the broker does not serve.
const gitRefusedMessage = "Jentic: only git clone, fetch and push over smart HTTP to https://github.com/<owner>/<repo> are relayed through Jentic.\n"

// webRefusedMessage answers any other request to github.com.
const webRefusedMessage = "Jentic: github.com is reachable from this session for git (clone, fetch, push) only.\n" +
	"Call the GitHub API at api.github.com (gh, curl) instead.\n"

// writeGitText answers with status and a text/plain body, which git shows to
// the user before it gives up.
func writeGitText(w http.ResponseWriter, status int, message string) {
	h := w.Header()
	h.Set("Content-Type", "text/plain; charset=utf-8")
	h.Set("Content-Length", strconv.Itoa(len(message)))
	h.Set("Cache-Control", "no-cache")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(message))
}

// gitResponse adjusts the broker's answer to a relayed git request. git
// meets a 401 by asking a credential helper or the terminal for a user name
// and password; the session holds no such credential (the proxy injects the
// invocation token on every request), so a 401 means the token was refused
// and no answer git could give would help. It is passed on as 403 without
// the challenge, so git prints the broker's message and stops instead of
// prompting.
func gitResponse(resp *http.Response) {
	if resp.StatusCode == http.StatusUnauthorized {
		resp.StatusCode = http.StatusForbidden
		resp.Status = strconv.Itoa(http.StatusForbidden) + " " + http.StatusText(http.StatusForbidden)
		resp.Header.Del("Www-Authenticate")
	}
}
