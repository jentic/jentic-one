// Package control also embeds the vendored OpenAPI spec (see SpecYAML) for the
// runtime `jentic api` passthrough. The generated client doc lives in client.go;
// this file adds only the hand-written embed beside spec.yaml.
package control

import _ "embed"

// SpecYAML is the vendored OpenAPI document this client was generated from,
// embedded verbatim. It is EXACTLY the spec `make generate-api` copied in and
// `check-cli-gen` pins to the SDK's commit, so the `jentic api` passthrough's
// path allowlist, self-description (`api describe`), and the generated client
// can never disagree about what the control plane offers (impl/5.0 §6a).
//
// This file is hand-written (not emitted by tools/specgen) precisely so the
// generator never clobbers it; go:embed cannot cross the module root, so the
// embed must live here beside spec.yaml rather than in the api command package.
//
//go:embed spec.yaml
var SpecYAML []byte
