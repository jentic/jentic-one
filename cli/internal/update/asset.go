package update

import "strings"

// AssetName returns the release archive filename for a binary, matching the
// per-binary goreleaser name_template introduced by the binary-distribution
// work (cli/.goreleaser.yaml: `jentic_{{.Version}}_{{.Os}}_{{.Arch}}`).
//
// GOOS/GOARCH already use goreleaser's os/arch vocabulary (linux/darwin/windows,
// amd64/arm64), so no mapping is needed beyond the windows→.zip switch that the
// `jentic` archive's format_overrides applies (jenticctl is not shipped for
// windows). goreleaser's {{.Version}} is the tag WITHOUT the leading "v", so we
// strip it here to stay byte-identical with the published asset — the installer's
// shell name construction and this helper are kept in lockstep and reviewed
// together (a golden test pins this to the YAML template).
//
// binary is "jentic" or "jenticctl"; version may be "v0.31.0" or "0.31.0".
func AssetName(binary, version, goos, goarch string) string {
	ver := strings.TrimPrefix(version, "v")
	ext := ".tar.gz"
	if goos == "windows" {
		ext = ".zip"
	}
	return binary + "_" + ver + "_" + goos + "_" + goarch + ext
}
