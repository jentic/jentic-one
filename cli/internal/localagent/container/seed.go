package container

import (
	"archive/tar"
	"bytes"
	"encoding/json"
	"fmt"
	"io/fs"
	"path"
	"sort"
	"strings"
	"time"
)

// HomeFile is one file the session writes into the agent's home, addressed
// relative to the home (".config/jentic/config.yaml").
type HomeFile struct {
	Rel  string
	Data []byte
	// Mode is the file's permission bits; zero means 0600.
	Mode fs.FileMode
}

// HomeArchive builds the tar that Session.Seed copies into the home volume:
// the home itself ("agent/", 0700) and every parent directory (0700), then
// each file, all owned by uid:gid. A path that is absolute, not clean or that
// leaves the home is refused.
func HomeArchive(uid, gid int, files []HomeFile) ([]byte, error) {
	root := path.Base(AgentHome)
	now := time.Now()
	dirs := map[string]bool{}
	byPath := map[string]HomeFile{}
	for _, f := range files {
		if f.Rel == "" || path.IsAbs(f.Rel) || path.Clean(f.Rel) != f.Rel || f.Rel == "." ||
			f.Rel == ".." || strings.HasPrefix(f.Rel, "../") {
			return nil, fmt.Errorf("refusing home file path %q", f.Rel)
		}
		if _, dup := byPath[f.Rel]; dup {
			return nil, fmt.Errorf("home file %q given twice", f.Rel)
		}
		byPath[f.Rel] = f
		for d := path.Dir(f.Rel); d != "."; d = path.Dir(d) {
			dirs[d] = true
		}
	}
	dirList := make([]string, 0, len(dirs))
	for d := range dirs {
		if _, clash := byPath[d]; clash {
			return nil, fmt.Errorf("home path %q is both a file and a directory", d)
		}
		dirList = append(dirList, d)
	}
	sort.Strings(dirList) // a parent sorts before its children
	fileList := make([]string, 0, len(byPath))
	for p := range byPath {
		fileList = append(fileList, p)
	}
	sort.Strings(fileList)

	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	header := func(name string, mode int64, typ byte, size int64) *tar.Header {
		return &tar.Header{
			Name: name, Mode: mode, Typeflag: typ, Size: size,
			Uid: uid, Gid: gid, ModTime: now, Format: tar.FormatPAX,
		}
	}
	for _, d := range append([]string{""}, dirList...) {
		if err := tw.WriteHeader(header(path.Join(root, d)+"/", 0o700, tar.TypeDir, 0)); err != nil {
			return nil, err
		}
	}
	for _, p := range fileList {
		f := byPath[p]
		mode := f.Mode.Perm()
		if mode == 0 {
			mode = 0o600
		}
		if err := tw.WriteHeader(header(path.Join(root, p), int64(mode), tar.TypeReg, int64(len(f.Data)))); err != nil {
			return nil, err
		}
		if _, err := tw.Write(f.Data); err != nil {
			return nil, err
		}
	}
	if err := tw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// claudeStateKeys are the ~/.claude.json keys copied from the operator's file:
// display preferences only. Account, API-key, OAuth and per-project state of
// the operator's own machine are never copied.
var claudeStateKeys = []string{"theme", "editorMode", "verbose", "preferredNotifChannel"}

// claudeSettingsKeys are the ~/.claude/settings.json keys copied from the
// operator's settings. Everything else is left out: hooks, the status line,
// apiKeyHelper and the AWS/OTel helpers run host commands that do not exist
// in the container, and env can carry provider credentials.
var claudeSettingsKeys = []string{
	"model",
	"permissions",
	"includeCoAuthoredBy",
	"cleanupPeriodDays",
	"outputStyle",
	"alwaysThinkingEnabled",
	"spinnerTipsEnabled",
}

// ClaudeSeed is the operator's Claude Code configuration offered to a
// container session: the raw ~/.claude.json and ~/.claude/settings.json, either
// nil when absent or when the operator declined seeding.
type ClaudeSeed struct {
	State    []byte
	Settings []byte
	// Memory is the operator's ~/.claude/CLAUDE.md.
	Memory []byte
}

// ClaudeHomeFiles builds the Claude Code files of a container session's home:
// a fresh ~/.claude.json that marks onboarding done and trusts workDir (the
// mounted work folder), plus the allowlisted keys of the operator's state and
// settings and their CLAUDE.md. No other file from the operator's ~/.claude
// is copied, so hooks, plugins, binaries and credential files never reach the
// container.
func ClaudeHomeFiles(workDir string, seed ClaudeSeed) ([]HomeFile, error) {
	state := map[string]any{}
	if err := pickKeys(seed.State, claudeStateKeys, state); err != nil {
		return nil, fmt.Errorf("read the operator's ~/.claude.json: %w", err)
	}
	state["hasCompletedOnboarding"] = true
	state["autoUpdates"] = false
	state["projects"] = map[string]any{
		workDir: map[string]any{
			"hasTrustDialogAccepted":        true,
			"hasCompletedProjectOnboarding": true,
		},
	}
	stateJSON, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return nil, err
	}
	files := []HomeFile{{Rel: ".claude.json", Data: stateJSON}}

	settings := map[string]any{}
	if err := pickKeys(seed.Settings, claudeSettingsKeys, settings); err != nil {
		return nil, fmt.Errorf("read the operator's ~/.claude/settings.json: %w", err)
	}
	if len(settings) > 0 {
		settingsJSON, err := json.MarshalIndent(settings, "", "  ")
		if err != nil {
			return nil, err
		}
		files = append(files, HomeFile{Rel: ".claude/settings.json", Data: settingsJSON})
	}
	if len(seed.Memory) > 0 {
		files = append(files, HomeFile{Rel: ".claude/CLAUDE.md", Data: seed.Memory})
	}
	return files, nil
}

// pickKeys copies keys of the JSON object raw into dst; empty raw is a no-op.
func pickKeys(raw []byte, keys []string, dst map[string]any) error {
	if len(bytes.TrimSpace(raw)) == 0 {
		return nil
	}
	var src map[string]any
	if err := json.Unmarshal(raw, &src); err != nil {
		return err
	}
	for _, k := range keys {
		if v, ok := src[k]; ok {
			dst[k] = v
		}
	}
	return nil
}
