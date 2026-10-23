// Package localagent holds the OS-level primitives behind `jentic run`: the
// known coding-agent descriptors, and the helpers that probe/grant/launch as a
// dedicated agent user. It is deliberately free of any cobra/config coupling so
// the command layer (internal/cli/*) stays a thin orchestrator over these.
//
// The security model this implements is documented in
// docs/security/same-host/local-agent-isolation.md: the agent runs as its own
// unprivileged Unix user, is granted access to individual working directories via
// inherited ACLs rather than by widening any human's home, and is launched under a
// per-session process-confinement profile (confine.go) that trims its view of the
// operator's home to just those grants.
package localagent
