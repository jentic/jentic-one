package core

import (
	"context"
	"os"
)

// notifyContextFunc has the signature of signal.NotifyContext; signalContext
// takes it as a parameter so tests can inject a fake subscription.
type notifyContextFunc func(parent context.Context, sig ...os.Signal) (context.Context, context.CancelFunc)

// signalContext returns a context cancelled by the first of sig, and releases
// the signal subscription as soon as that happens.
//
// Subscribing to a signal replaces its default disposition for the whole
// process, so holding the subscription for the process lifetime means every
// signal after the first is silently discarded: a command blocked somewhere
// that never observes ctx (huh's accessible-mode stdin loop, a stuck syscall)
// becomes unkillable by Ctrl-C (jentic-one#841). Calling stop on the first
// cancellation restores the default disposition, so the first Ctrl-C asks the
// command to unwind gracefully and a second one terminates the process
// immediately — the usual Go CLI convention.
//
// The returned stop is idempotent and must still be deferred by the caller so
// the subscription is released when the command finishes without a signal.
func signalContext(parent context.Context, notify notifyContextFunc, sig ...os.Signal) (context.Context, context.CancelFunc) {
	ctx, stop := notify(parent, sig...)
	go func() {
		<-ctx.Done()
		stop()
	}()
	return ctx, stop
}
