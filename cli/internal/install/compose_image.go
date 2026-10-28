package install

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

// ComposeAppImage returns the image reference the compose file's app service
// runs, or "" when there is no compose file or it names no image for that
// service.
//
// The value records how the stack was installed, and is the only place that is
// recorded: a published reference such as ghcr.io/jentic/jentic-one-app:0.44.0
// (written by the pull path) means the stack runs a released image, and
// AppImageTag means it runs one built from source. config.Manifest carries the
// deploy mode but not this distinction, and reading it here keeps installs
// written before this check from needing a manifest migration.
//
// Only the app service is read: its name is fixed in the compose template,
// while the broker's is configurable. Both render the same image.
func ComposeAppImage(composePath string) (string, error) {
	raw, err := os.ReadFile(composePath) //nolint:gosec // composePath is the CLI-managed ~/.jentic compose file.
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", nil
		}
		return "", fmt.Errorf("read compose file: %w", err)
	}
	var doc struct {
		Services map[string]struct {
			Image string `yaml:"image"`
		} `yaml:"services"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return "", fmt.Errorf("parse compose file: %w", err)
	}
	return strings.TrimSpace(doc.Services[composeServiceApp].Image), nil
}

// SetComposeAppImage points the compose file's app and broker services at
// image, reporting whether the file changed.
//
// The app and broker services render the same image, so swapping the current
// value covers both; the database service renders its own image, so it is never
// matched. Rewriting just the value — rather than re-rendering the file from a
// Draft, which an update has no way to reconstruct faithfully — keeps an
// operator's own edits to ports, volumes and apps intact.
func SetComposeAppImage(composePath, image string) (bool, error) {
	current, err := ComposeAppImage(composePath)
	if err != nil {
		return false, err
	}
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
	updated := strings.ReplaceAll(string(raw), "image: "+current, "image: "+image)
	if updated == string(raw) {
		return false, fmt.Errorf("could not rewrite the %q service image in %s", composeServiceApp, composePath)
	}
	if err := os.WriteFile(composePath, []byte(updated), 0o600); err != nil { //nolint:gosec // composePath is the CLI-managed ~/.jentic compose file, not user input.
		return false, fmt.Errorf("write %s: %w", composePath, err)
	}
	return true, nil
}
