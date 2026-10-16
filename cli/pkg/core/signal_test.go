package core

import (
	"context"
	"os"
	"syscall"
	"testing"
	"time"
)

// fakeNotify stands in for signal.NotifyContext: it returns a context the test
// cancels by hand (simulating the first signal) and records stop calls.
type fakeNotify struct {
	cancel  context.CancelFunc
	stopped chan struct{}
	sigs    []os.Signal
}

func newFakeNotify() *fakeNotify { return &fakeNotify{stopped: make(chan struct{}, 8)} }

func (f *fakeNotify) notify(parent context.Context, sig ...os.Signal) (context.Context, context.CancelFunc) {
	f.sigs = sig
	ctx, cancel := context.WithCancel(parent)
	f.cancel = cancel
	return ctx, func() {
		cancel()
		f.stopped <- struct{}{}
	}
}

func TestSignalContextStopsSubscriptionOnFirstSignal(t *testing.T) {
	f := newFakeNotify()
	ctx, stop := signalContext(context.Background(), f.notify, syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	if len(f.sigs) != 2 || f.sigs[0] != syscall.SIGINT || f.sigs[1] != syscall.SIGTERM {
		t.Fatalf("subscribed to %v, want [SIGINT SIGTERM]", f.sigs)
	}
	select {
	case <-f.stopped:
		t.Fatal("stop ran before any signal arrived")
	case <-time.After(20 * time.Millisecond):
	}

	f.cancel() // the first signal
	select {
	case <-ctx.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("ctx not cancelled by the first signal")
	}
	select {
	case <-f.stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("stop not called after the first signal; a second Ctrl-C would be swallowed")
	}
}

func TestSignalContextStopsWhenParentCancelled(t *testing.T) {
	f := newFakeNotify()
	parent, cancel := context.WithCancel(context.Background())
	_, stop := signalContext(parent, f.notify, syscall.SIGINT)
	defer stop()

	cancel()
	select {
	case <-f.stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("stop not called after parent cancellation (watcher goroutine leaked)")
	}
}
