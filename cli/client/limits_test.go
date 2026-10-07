package client

import (
	"bytes"
	"errors"
	"io"
	"strings"
	"testing"
)

func TestReadAllBounded_UnderLimit(t *testing.T) {
	src := []byte("hello world")
	got, err := ReadAllBounded(bytes.NewReader(src), 1024)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !bytes.Equal(got, src) {
		t.Errorf("got %q, want %q", got, src)
	}
}

func TestReadAllBounded_ExactlyAtLimit(t *testing.T) {
	src := bytes.Repeat([]byte("a"), 100)
	got, err := ReadAllBounded(bytes.NewReader(src), 100)
	if err != nil {
		t.Fatalf("a body of exactly max bytes must be accepted, got error: %v", err)
	}
	if len(got) != 100 {
		t.Errorf("got %d bytes, want 100", len(got))
	}
}

func TestReadAllBounded_OverLimitFailsClosed(t *testing.T) {
	src := bytes.Repeat([]byte("a"), 101)
	got, err := ReadAllBounded(bytes.NewReader(src), 100)
	if err == nil {
		t.Fatal("expected ErrBodyTooLarge for a body over the limit, got nil")
	}
	if !errors.Is(err, ErrBodyTooLarge) {
		t.Errorf("error should be ErrBodyTooLarge, got %v", err)
	}
	if got != nil {
		t.Errorf("no bytes should be returned on overflow, got %d", len(got))
	}
}

// TestReadAllBounded_NonPositiveMaxUsesDefault ensures a caller can never
// accidentally disable the cap by passing 0 (or a negative) — it clamps to
// MaxBodyBytes instead of reading without bound.
func TestReadAllBounded_NonPositiveMaxUsesDefault(t *testing.T) {
	src := []byte("small")
	got, err := ReadAllBounded(bytes.NewReader(src), 0)
	if err != nil {
		t.Fatalf("unexpected error with max=0: %v", err)
	}
	if !bytes.Equal(got, src) {
		t.Errorf("got %q, want %q", got, src)
	}
}

// errReader returns an error partway through to confirm ReadAllBounded surfaces
// underlying transport errors (not just size refusals).
type errReader struct{ msg string }

func (e errReader) Read([]byte) (int, error) { return 0, errors.New(e.msg) }

func TestReadAllBounded_PropagatesReadError(t *testing.T) {
	_, err := ReadAllBounded(errReader{msg: "boom"}, 100)
	if err == nil || !strings.Contains(err.Error(), "boom") {
		t.Errorf("expected underlying read error to propagate, got %v", err)
	}
	if errors.Is(err, ErrBodyTooLarge) {
		t.Errorf("a transport error must not be classified as ErrBodyTooLarge: %v", err)
	}
}

// TestReadAllBounded_DefaultCeilingRefusesHugeBody exercises the real MaxBodyBytes
// ceiling with a reader that would produce more than 64 MiB, without allocating
// it: an infinite reader is clamped and refused.
func TestReadAllBounded_DefaultCeilingRefusesHugeBody(t *testing.T) {
	// io.LimitReader inside ReadAllBounded reads at most MaxBodyBytes+1, so this
	// allocates ~64 MiB transiently then fails closed — acceptable for one test.
	_, err := ReadAllBounded(neverEndingReader{}, MaxBodyBytes)
	if !errors.Is(err, ErrBodyTooLarge) {
		t.Errorf("an unbounded body must be refused with ErrBodyTooLarge, got %v", err)
	}
}

// neverEndingReader yields zero bytes forever (never EOF), modelling a hostile
// peer that streams without end.
type neverEndingReader struct{}

func (neverEndingReader) Read(p []byte) (int, error) { return len(p), nil }

var _ io.Reader = neverEndingReader{}
