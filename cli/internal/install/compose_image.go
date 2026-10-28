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

// composeDoc is a compose file as read back off disk: the raw bytes and the
// parsed tree, from one read, so a caller that both inspects and rewrites the
// file never matches against a different revision than it writes.
type composeDoc struct {
	raw  []byte
	root yaml.Node
}

// readComposeDoc reads and parses the compose file, or returns (nil, nil) when
// there is no such file. It is the one place that decides how a compose file is
// read back, so the missing-file rule and the error wording stay in step across
// callers. (composeHostBindings walks its own node in ports.go because it needs
// the services in file order.)
func readComposeDoc(composePath string) (*composeDoc, error) {
	raw, err := os.ReadFile(composePath) //nolint:gosec // composePath is the CLI-managed ~/.jentic compose file.
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, fmt.Errorf("read compose file: %w", err)
	}
	doc := &composeDoc{raw: raw}
	if err := yaml.Unmarshal(raw, &doc.root); err != nil {
		return nil, fmt.Errorf("parse compose file: %w", err)
	}
	return doc, nil
}

// mappingValue returns the value node for key in a mapping node, or nil.
// A mapping node's Content alternates key, value.
func mappingValue(node *yaml.Node, key string) *yaml.Node {
	if node == nil {
		return nil
	}
	if node.Kind == yaml.DocumentNode && len(node.Content) == 1 {
		node = node.Content[0]
	}
	if node.Kind != yaml.MappingNode {
		return nil
	}
	for i := 0; i+1 < len(node.Content); i += 2 {
		if node.Content[i].Value == key {
			return node.Content[i+1]
		}
	}
	return nil
}

// appImageServices are the services the compose template renders the app image
// for. A repoint must move all of them together: leaving one behind runs the
// app and the broker on different versions, with migrations from one applied
// against a peer on the other.
var appImageServices = []string{composeServiceApp, composeServiceBroker}

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
// Reading the app service alone is enough to identify the install: the template
// renders the same image for app and for the broker. A repoint still moves both
// (see appImageServices).
func ComposeAppImage(composePath string) (string, error) {
	doc, err := readComposeDoc(composePath)
	if err != nil || doc == nil {
		return "", err
	}
	return doc.imageOf(composeServiceApp)
}

// imageOf returns one service's image value, or "" when it has none. An alias
// is reported as an error: the value lives at the anchor, not on this line, so
// a rewrite scoped to the line cannot move it.
func (d *composeDoc) imageOf(service string) (string, error) {
	node := mappingValue(mappingValue(&d.root, "services"), service)
	img := mappingValue(node, "image")
	if img == nil {
		return "", nil
	}
	if img.Kind == yaml.AliasNode {
		return "", fmt.Errorf(
			"the %q service's image is a YAML alias (*%s), which this tool cannot rewrite; "+
				"inline the image or edit it by hand", service, img.Value)
	}
	return strings.TrimSpace(img.Value), nil
}

// imageLine matches a compose `image:` entry, splitting it into indentation,
// the value with any quotes still on, and any trailing comment. Anchored on a
// line the YAML parser has already identified, so it never has to decide which
// block the line belongs to.
var imageLine = regexp.MustCompile(`^(\s*image:[ \t]*)("[^"]*"|'[^']*'|[^\s#]*)([ \t]*(?:#.*)?)$`)

// SetComposeAppImage points the compose file's app and broker services at
// image, reporting whether the file changed.
//
// Which lines to touch comes from the YAML parser, so the rewrite is scoped to
// those two services: a `x-` extension block or an anchor carrying the same
// value is left alone, and so is a service whose image merely starts with the
// current one. Only the value on each located line is replaced — indentation,
// quoting and any trailing comment survive — rather than re-rendering the file
// from a Draft, which an update has no way to reconstruct faithfully, so an
// operator's own edits to ports, volumes and apps are kept.
func SetComposeAppImage(composePath, image string) (bool, error) {
	doc, err := readComposeDoc(composePath)
	if err != nil {
		return false, err
	}
	if doc == nil {
		return false, fmt.Errorf("no compose file at %s", composePath)
	}

	lines := splitLines(string(doc.raw))
	changed := false
	for _, service := range appImageServices {
		node := mappingValue(mappingValue(&doc.root, "services"), service)
		if node == nil {
			// A single-surface stack may not run the broker at all.
			continue
		}
		img := mappingValue(node, "image")
		if img == nil {
			return false, fmt.Errorf("the %q service in %s names no image", service, composePath)
		}
		if img.Kind == yaml.AliasNode {
			return false, fmt.Errorf(
				"the %q service's image is a YAML alias (*%s), which this tool cannot rewrite; "+
					"inline the image or edit it by hand", service, img.Value)
		}
		if strings.TrimSpace(img.Value) == image {
			continue
		}
		if err := setImageOnLine(lines, img.Line, image, service, composePath); err != nil {
			return false, err
		}
		changed = true
	}
	if !changed {
		return false, nil
	}
	if err := atomicWriteFile(composePath, []byte(strings.Join(lines, "\n"))); err != nil {
		return false, err
	}
	return true, nil
}

// setImageOnLine replaces the value on the 1-based line the parser reported for
// a service's image, keeping the line's indentation, quoting, trailing comment
// and line ending.
func setImageOnLine(lines []string, line int, image, service, composePath string) error {
	if line < 1 || line > len(lines) {
		return fmt.Errorf("the %q service's image in %s is on line %d, which does not exist",
			service, composePath, line)
	}
	i := line - 1
	body, eol := splitEOL(lines[i])
	m := imageLine.FindStringSubmatch(body)
	if m == nil {
		return fmt.Errorf("cannot rewrite the %q service's image on line %d of %s: %q",
			service, line, composePath, body)
	}
	quote := ""
	if len(m[2]) >= 2 && (strings.HasPrefix(m[2], `"`) || strings.HasPrefix(m[2], `'`)) {
		quote = m[2][:1]
	}
	lines[i] = m[1] + quote + image + quote + m[3] + eol
	return nil
}

// splitLines splits on "\n" only, so a CRLF file keeps its "\r" at the end of
// each line and is written back with its own endings intact.
func splitLines(body string) []string { return strings.Split(body, "\n") }

// splitEOL separates a trailing "\r" from a line's content so a CRLF file's
// endings survive a rewrite.
func splitEOL(line string) (body, eol string) {
	if strings.HasSuffix(line, "\r") {
		return line[:len(line)-1], "\r"
	}
	return line, ""
}

// atomicWriteFile replaces path's contents via a temp file in the same
// directory and a rename, keeping the destination's existing mode, and fsyncs
// the directory so the rename itself is durable.
//
// The compose file holds the operator's own edits and the managed database
// password, and no update can regenerate it, so a truncating write could lose
// it outright. Losing only the rename is nearly as bad: the pull and the
// migrations have already happened, so the stack would boot the old image
// against the new schema.
//
// Near-identical helpers live in internal/config, internal/mcpcfg and
// internal/skillgen; they are worth promoting to one shared implementation.
func atomicWriteFile(path string, data []byte) error {
	perm := os.FileMode(0o600)
	if info, err := os.Stat(path); err == nil {
		perm = info.Mode().Perm()
	}
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
	return syncDir(dir)
}

// syncDir flushes a directory entry so a completed rename survives a crash.
func syncDir(dir string) error {
	d, err := os.Open(dir) //nolint:gosec // dir is the CLI-managed ~/.jentic install root.
	if err != nil {
		return fmt.Errorf("open %s: %w", dir, err)
	}
	defer func() { _ = d.Close() }()
	if err := d.Sync(); err != nil {
		return fmt.Errorf("sync %s: %w", dir, err)
	}
	return nil
}
