package paginate

import (
	"context"
	"errors"
	"testing"
)

func fetcher(pages [][]int) PageFn[int] {
	calls := 0
	return func(_ context.Context, cursor string) (Page[int], error) {
		idx := calls
		calls++
		p := Page[int]{Items: pages[idx]}
		if idx+1 < len(pages) {
			p.Next = "cursor-" + cursor + "x" // any non-empty value
		}
		return p, nil
	}
}

func TestAll_WalksEveryPage(t *testing.T) {
	got, err := All(context.Background(), fetcher([][]int{{1, 2}, {3}, {4, 5}}))
	if err != nil {
		t.Fatal(err)
	}
	want := []int{1, 2, 3, 4, 5}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

func TestAll_SinglePage(t *testing.T) {
	got, err := All(context.Background(), fetcher([][]int{{9}}))
	if err != nil || len(got) != 1 || got[0] != 9 {
		t.Fatalf("got %v, err %v", got, err)
	}
}

func TestForEach_YieldErrorStops(t *testing.T) {
	sentinel := errors.New("stop")
	seen := 0
	err := ForEach(context.Background(), fetcher([][]int{{1}, {2}, {3}}), func(Page[int]) error {
		seen++
		return sentinel
	})
	if !errors.Is(err, sentinel) {
		t.Fatalf("err = %v, want sentinel", err)
	}
	if seen != 1 {
		t.Fatalf("yield called %d times, want 1 (walk must stop on first error)", seen)
	}
}

func TestForEach_FetchError(t *testing.T) {
	boom := errors.New("boom")
	err := ForEach(context.Background(), func(context.Context, string) (Page[int], error) {
		return Page[int]{}, boom
	}, func(Page[int]) error { return nil })
	if !errors.Is(err, boom) {
		t.Fatalf("err = %v, want boom", err)
	}
}

func TestForEach_ContextCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := ForEach(ctx, fetcher([][]int{{1}, {2}}), func(Page[int]) error { return nil })
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
}

func TestForEach_RunawayCursorBounded(t *testing.T) {
	// A fetcher that ALWAYS reports a next cursor must hit the safety bound and
	// return tooManyPagesError, never spin forever.
	err := ForEach(context.Background(), func(context.Context, string) (Page[int], error) {
		return Page[int]{Items: []int{0}, Next: "always"}, nil
	}, func(Page[int]) error { return nil })
	var tooMany *tooManyPagesError
	if !errors.As(err, &tooMany) {
		t.Fatalf("err = %v, want tooManyPagesError", err)
	}
}
