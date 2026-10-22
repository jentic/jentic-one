// Package egressproxy is the egress proxy of `jentic run --container`: an
// HTTP CONNECT proxy that runs in its own sidecar container and is the agent
// container's only route beyond Jentic.
//
// Every CONNECT is classified by Policy, in this order:
//
//   - a host on the agent's tunnel allowlist (its LLM provider) is spliced
//     through without terminating TLS;
//   - a host at or below a governed vendor domain (github.com,
//     api.cloudflare.com, amazonaws.com) is TLS-terminated with a leaf from the
//     per-session CA, and each HTTP request on it is relayed to the broker's
//     `/_facade/proxy/https://<host><path>?<query>` route with an invocation
//     token as its bearer: the vendor credential is injected by the broker,
//     under the agent's rules, and never reaches the session. On github.com
//     itself only git smart HTTP (clone, fetch, push) is relayed, to the
//     broker's governed git pipeline, which checks the agent's git rules per
//     repository and ref; every other github.com request (web pages, Git
//     LFS, dumb HTTP) is refused with a message git or curl prints;
//   - everything else, plain-HTTP proxying included, is refused with 403 and
//     logged.
//
// The session CA (CA) is generated in memory at start (ECDSA P-256, pathlen:0,
// critical nameConstraints permitting the governed domains only and excluding
// every IP address). Its key never leaves the process; only the certificate,
// a bundle of the system roots plus the certificate, and a ready file carrying
// the session's readiness nonce are published (Publish), to a volume the agent
// mounts read-only.
//
// A governed request uses the per-command token from the CONNECT's Basic
// proxy credentials (`HTTPS_PROXY=http://cit_…:x@proxy:3128`) when there is
// one, else the session token for the vendor, which the host-side CLI mints,
// renews and writes to a token file on the proxy's private tmpfs
// (WriteTokenFile). With neither, the CONNECT is answered 407 with a
// Proxy-Authenticate challenge on the same connection.
//
// Like its parent package this one is stdlib-only.
package egressproxy
