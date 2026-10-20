package container

import (
	"context"
	"fmt"
)

// Egress is a session's route to anything beyond Jentic. The agent shares the
// loopback container's network namespace, whose only interface besides lo is
// the session's internal network, so whatever an Egress adds to that network
// is all the agent can reach besides Jentic's loopback ports.
//
// A session asks its Egress for sidecars to start on the internal network
// before the agent, for the agent's extra environment and mounts, and waits
// for Ready before it starts the agent. An implementation must fail closed:
// if it cannot set up its route, Ready returns an error and the session does
// not start.
type Egress interface {
	// Name is the egress mode the operator chose, e.g. "none".
	Name() string
	// Sidecars are the containers this egress runs for the session; each is
	// attached to the session's internal network (Plan.Network) and labelled
	// with the session.
	Sidecars(p *Plan) []Spec
	// AgentEnv is extra KEY=VALUE environment for the agent container.
	AgentEnv(p *Plan) []string
	// AgentMounts are extra mounts for the agent container; they must be
	// read-only.
	AgentMounts(p *Plan) []Mount
	// Ready blocks until the sidecars accept traffic, or fails.
	Ready(ctx context.Context, d Docker, p *Plan) error
}

// EgressNone is the egress mode with no route beyond Jentic.
const EgressNone = "none"

// NoEgress gives the agent no route beyond Jentic: no sidecar, no extra
// environment. The agent reaches Jentic at its loopback URLs and nothing
// else, so calls to an LLM provider or a vendor API fail inside the
// container.
type NoEgress struct{}

// Name implements Egress.
func (NoEgress) Name() string { return EgressNone }

// Sidecars implements Egress.
func (NoEgress) Sidecars(*Plan) []Spec { return nil }

// AgentEnv implements Egress.
func (NoEgress) AgentEnv(*Plan) []string { return nil }

// AgentMounts implements Egress.
func (NoEgress) AgentMounts(*Plan) []Mount { return nil }

// Ready implements Egress.
func (NoEgress) Ready(context.Context, Docker, *Plan) error { return nil }

// EgressModes lists the egress modes ResolveEgress accepts.
func EgressModes() []string { return []string{EgressNone} }

// ResolveEgress returns the Egress for mode.
func ResolveEgress(mode string) (Egress, error) {
	switch mode {
	case "", EgressNone:
		return NoEgress{}, nil
	default:
		return nil, fmt.Errorf("unknown container egress mode %q (supported: %s)", mode, EgressNone)
	}
}
