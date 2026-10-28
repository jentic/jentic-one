package install

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

// composeServiceView is the slice of a compose service this package reads back
// out of a generated file.
type composeServiceView struct {
	Image string `yaml:"image"`
}

// readComposeServices decodes the compose file's services block, or nil when
// there is no such file. It is the one place that decides how a compose file is
// read back, so the missing-file rule and the error wording stay in step across
// callers. (composeHostBindings walks a yaml.Node instead, because it needs the
// services in file order.)
func readComposeServices(composePath string) (map[string]composeServiceView, error) {
	raw, err := os.ReadFile(composePath) //nolint:gosec // composePath is the CLI-managed ~/.jentic compose file.
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, fmt.Errorf("read compose file: %w", err)
	}
	var doc struct {
		Services map[string]composeServiceView `yaml:"services"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("parse compose file: %w", err)
	}
	return doc.Services, nil
}

// ComposeAppImage returns the image reference the compose file's app service
// runs, or "" when there is no compose file or it names no image for that
// service.
//
// The value is the record of what the stack actually runs, and the only one:
// a published reference such as ghcr.io/jentic/jentic-one-app:0.44.0 (written
// by the pull path) means the stack runs a released image, and AppImageTag
// means it runs one built from source. config.Manifest carries the deploy mode
// but neither this distinction nor the registry, and reading the deployed file
// cannot go stale the way a record written once at install time can.
//
// Reading the app service alone is enough: the template renders the same image
// for app and for the broker, so the two never disagree.
func ComposeAppImage(composePath string) (string, error) {
	services, err := readComposeServices(composePath)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(services[composeServiceApp].Image), nil
}

// imageLine matches a compose `image:` entry, capturing its indentation and the
// value with any surrounding quotes left on.
var imageLine = regexp.MustCompile(`^(\s*)image:[ \t]*(.*?)[ \t]*$`)

// SetComposeAppImage points every compose service running current at image,
// reporting whether the file changed. current is the value the caller already
// read (see ComposeAppImage), so the file is not re-parsed to re-derive it.
//
// Rewriting the matched lines — rather than re-rendering the file from a Draft,
// which an update has no way to reconstruct faithfully — keeps an operator's
// own edits to ports, volumes and apps intact. Matching is per line and on the
// whole value, so a service whose image merely starts with current (a
// `:0.43.0-debug` sidecar beside `:0.43.0`) is left alone, as is the database
// service, which renders its own image.
func SetComposeAppImage(composePath, current, image string) (bool, error) {
	if current == "" {
		return false, fmt.Errorf("no %q service image in %s", composeServiceApp, composePath)
	}
	if current == image {
		return false, nil
	}
	raw, err := os.ReadFile(composePath) //nolint:gosec // composePath is the CLI-managed ~/.jentic compose file.
	if err != nil {
		return false, fmt.Errorf("read compose file: %w", err)
	}
	updated, n := setImageLines(string(raw), current, image)
	if n == 0 {
		return false, fmt.Errorf(
			"could not find an image line for %s in %s — edit it by hand to %s", current, composePath, image)
	}
	if err := atomicWriteFile(composePath, []byte(updated), 0o600); err != nil {
		return false, err
	}
	return true, nil
}

// setImageLines replaces the value of every `image:` line that names current,
// preserving each line's indentation and quoting, and reports how many it
// rewrote.
func setImageLines(body, current, image string) (string, int) {
	lines := strings.Split(body, "\n")
	count := 0
	for i, line := range lines {
		m := imageLine.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		value, quote := unquoteImageValue(m[2])
		if value != current {
			continue
		}
		lines[i] = m[1] + "image: " + quote + image + quote
		count++
	}
	return strings.Join(lines, "\n"), count
}

// unquoteImageValue strips a matching pair of surrounding quotes, returning the
// bare value and the quote character to write back (empty when unquoted).
func unquoteImageValue(raw string) (value, quote string) {
	if len(raw) >= 2 {
		for _, q := range []string{`"`, `'`} {
			if strings.HasPrefix(raw, q) && strings.HasSuffix(raw, q) {
				return raw[1 : len(raw)-1], q
			}
		}
	}
	return raw, ""
}

// atomicWriteFile replaces path's contents via a temp file in the same
// directory and a rename, so an interrupted write cannot truncate what is
// already there. The compose file holds the operator's own edits and the
// managed database password, and no update can regenerate it, so a partial
// write would be unrecoverable.
func atomicWriteFile(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, filepath.Base(path)+".tmp*")
	if err != nil {
		return fmt.Errorf("create temp file in %s: %w", dir, err)
	}
	tmpName := tmp.Name()
	defer func() {
		// Best-effort cleanup of the staging file: on the success path the
		// rename has already consumed it, and on a failure path the error being
		// returned is the one worth reporting.
		_ = tmp.Close()
		_ = os.Remove(tmpName)
	}()
	if _, err := tmp.Write(data); err != nil {
		return fmt.Errorf("write %s: %w", tmpName, err)
	}
	if err := tmp.Sync(); err != nil {
		return fmt.Errorf("sync %s: %w", tmpName, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close %s: %w", tmpName, err)
	}
	if err := os.Chmod(tmpName, perm); err != nil {
		return fmt.Errorf("chmod %s: %w", tmpName, err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("replace %s: %w", path, err)
	}
	return nil
}
