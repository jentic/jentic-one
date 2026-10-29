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
	// The LAST match, because that is the one compose uses when a mapping
	// carries the same key twice. Taking the first would rewrite a line the
	// running stack ignores and leave the effective one behind.
	var found *yaml.Node
	for i := 0; i+1 < len(node.Content); i += 2 {
		if node.Content[i].Value == key {
			found = node.Content[i+1]
		}
	}
	return found
}

// imageScalar returns the scalar node holding a service's image, or an error
// when its shape cannot be rewritten in place.
//
// The repoint replaces the value on the single line the parser reports, so
// anything whose value does not live plainly on that line has to be refused
// rather than half-written: an alias (the value is at the anchor), a block
// scalar (the line holds only the `>-`/`|` indicator), and a service that
// inherits its image through a merge key (there is no own `image` line at all).
//
// A service that exists but whose image cannot be read is an error, never an
// empty string: reporting "" would make the caller treat the stack as built
// from source, build, and leave the compose file naming the published image it
// already ran — the exact failure #2011 is about.
func imageScalar(service *yaml.Node, name string) (*yaml.Node, error) {
	img := mappingValue(service, "image")
	if img == nil {
		return nil, fmt.Errorf(
			"the %q service names no image of its own; this tool cannot follow a merge key or an "+
				"inherited image — set the image on the service, or edit it by hand", name)
	}
	if img.Kind == yaml.AliasNode {
		return nil, fmt.Errorf(
			"the %q service's image is a YAML alias (*%s), whose value lives at the anchor; "+
				"inline the image or edit it by hand", name, img.Value)
	}
	switch img.Style {
	case 0, yaml.SingleQuotedStyle, yaml.DoubleQuotedStyle:
		return img, nil
	default:
		return nil, fmt.Errorf(
			"the %q service's image is not a plain scalar, so its value is not on one line; "+
				"write it as `image: <reference>`, or edit it by hand", name)
	}
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
	if node == nil {
		// No such service: a single-surface stack may not run the broker, and a
		// file with no services at all has nothing to identify.
		return "", nil
	}
	img, err := imageScalar(node, service)
	if err != nil {
		return "", err
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
	return setComposeAppImage(composePath, image, false)
}

// CheckComposeAppImageWritable reports whether a repoint of this compose file
// would succeed, without writing anything.
//
// It exists so a caller can refuse up front rather than after pulling or
// building an image: every reason the rewrite can fail is a static property of
// the file (a shape it cannot rewrite, a line it cannot match), so discovering
// them afterwards leaves an image fetched, the compose file untouched, and no
// retry that can behave differently.
func CheckComposeAppImageWritable(composePath, image string) error {
	_, err := setComposeAppImage(composePath, image, true)
	return err
}

func setComposeAppImage(composePath, image string, dryRun bool) (bool, error) {
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
		img, err := imageScalar(node, service)
		if err != nil {
			return false, err
		}
		if strings.TrimSpace(img.Value) == image {
			continue
		}
		if err := setImageOnLine(lines, img.Line, image, service, composePath); err != nil {
			return false, err
		}
		changed = true
	}
	if !changed || dryRun {
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
// directory and a rename, keeping the destination's existing mode.
//
// The compose file holds the operator's own edits and the managed database
// password, and no update can regenerate it, so a truncating write could lose
// it outright: a reader either sees the old file or the new one, never a
// half-written one. The new contents are fsynced before the rename; the
// directory entry is flushed too where the platform allows it (see syncDir),
// so on a host that does not, a crash in the instant after the rename can
// still lose the repoint.
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
	syncDir(dir)
	return nil
}

// syncDir flushes a directory entry so a completed rename is durable, where the
// platform supports it.
//
// Best-effort on purpose: flushing a directory handle is a POSIX notion and
// fails outright on Windows ("Access is denied"), where the rename carries its
// own ordering. Returning that error would make every repoint fail there, so
// the sync is attempted and its result dropped — the file's own contents are
// already fsynced before the rename either way.
func syncDir(dir string) {
	d, err := os.Open(dir) //nolint:gosec // dir is the CLI-managed ~/.jentic install root.
	if err != nil {
		return
	}
	_ = d.Sync()
	_ = d.Close()
}
