package agentops

import (
	"bytes"
	"net/http"
	"reflect"
	"testing"
)

func TestEnvelopeWithin_SkipsDecodeWhenOversize(t *testing.T) {
	const capBytes int64 = 64
	body := append([]byte(`{"items":["`), bytes.Repeat([]byte("a"), int(capBytes))...)
	body = append(body, []byte(`"]}`)...)
	if int64(len(body)) <= capBytes {
		t.Fatalf("test setup: len(body) = %d, want > %d", len(body), capBytes)
	}

	res := &ExecuteResult{
		Status: http.StatusOK,
		Headers: http.Header{
			"Content-Type":        {"application/json"},
			"Jentic-Execution-Id": {"exec_oversize"},
			"X-Multi":             {"first", "second"},
		},
		Body:        body[:capBytes+1],
		ExecutionID: "exec_oversize",
	}

	want := res.Envelope()
	got, oversize := res.EnvelopeWithin(capBytes)
	if !oversize {
		t.Fatalf("oversize = false, want true for %d-byte body with %d-byte cap", len(res.Body), capBytes)
	}
	if got.Body != nil {
		t.Errorf("got.Body = %#v, want nil when body exceeds cap", got.Body)
	}
	if !reflect.DeepEqual(got.Headers, want.Headers) {
		t.Errorf("got.Headers = %#v, want %#v", got.Headers, want.Headers)
	}
	if got.Status != want.Status || got.ExecutionID != want.ExecutionID || got.SchemaVersion != want.SchemaVersion {
		t.Errorf("got envelope metadata (%d, %q, %q), want (%d, %q, %q)",
			got.Status, got.ExecutionID, got.SchemaVersion,
			want.Status, want.ExecutionID, want.SchemaVersion)
	}
}

func TestEnvelopeWithin_EqualsEnvelopeUnderCap(t *testing.T) {
	headers := http.Header{
		"Content-Type": {"application/json"},
		"X-Trace":      {"trace-1"},
	}
	cases := []struct {
		name  string
		body  []byte
		limit int64
	}{
		{
			name:  "json object under cap",
			body:  []byte(`{"ok":true,"count":3}`),
			limit: 128,
		},
		{
			name:  "json object at exact cap",
			body:  []byte(`{"ok":true}`),
			limit: int64(len(`{"ok":true}`)),
		},
		{
			name:  "non-json plain text under cap",
			body:  []byte("plain text upstream body"),
			limit: 128,
		},
		{
			name:  "empty body under cap",
			body:  nil,
			limit: 128,
		},
		{
			name:  "non-positive limit disables cap",
			body:  []byte(`{"large":true}`),
			limit: 0,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			res := &ExecuteResult{
				Status:      http.StatusCreated,
				Headers:     headers,
				Body:        tc.body,
				ExecutionID: "exec_123",
			}
			want := res.Envelope()
			got, oversize := res.EnvelopeWithin(tc.limit)
			if oversize {
				t.Fatalf("oversize = true, want false (len=%d, limit=%d)", len(tc.body), tc.limit)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("EnvelopeWithin(%d) = %#v, want %#v", tc.limit, got, want)
			}
		})
	}
}

func TestEnvelopeWithin_AllocBound(t *testing.T) {
	const (
		bodySize = 4 << 20   // 4 MiB JSON array
		capBytes = 128 << 10 // 128 KiB cap
	)
	var buf bytes.Buffer
	buf.Grow(bodySize + 64)
	buf.WriteByte('[')
	for buf.Len() < bodySize {
		if buf.Len() > 1 {
			buf.WriteByte(',')
		}
		buf.WriteString(`{"id":12345,"name":"item","active":true}`)
	}
	buf.WriteByte(']')

	res := &ExecuteResult{
		Status: http.StatusOK,
		Headers: http.Header{
			"Content-Type":        {"application/json"},
			"Jentic-Execution-Id": {"exec_alloc"},
		},
		Body:        buf.Bytes(),
		ExecutionID: "exec_alloc",
	}

	var sawNonNilBody, sawNotOversize bool
	allocs := testing.AllocsPerRun(20, func() {
		env, oversize := res.EnvelopeWithin(capBytes)
		if !oversize {
			sawNotOversize = true
		}
		if env.Body != nil {
			sawNonNilBody = true
		}
	})
	if sawNotOversize || sawNonNilBody {
		t.Fatalf("EnvelopeWithin(%d): oversize=%v, nonNilBody=%v; want oversize=true and Body=nil",
			capBytes, !sawNotOversize, sawNonNilBody)
	}
	if allocs >= 50 {
		t.Fatalf("allocs = %.1f, want < 50 when skipping oversize JSON decode", allocs)
	}
}

func BenchmarkEnvelopeWithin_Oversize(b *testing.B) {
	const (
		bodySize = 8 << 20   // 8 MiB JSON array
		capBytes = 128 << 10 // 128 KiB cap
	)
	var buf bytes.Buffer
	buf.Grow(bodySize + 64)
	buf.WriteByte('[')
	for buf.Len() < bodySize {
		if buf.Len() > 1 {
			buf.WriteByte(',')
		}
		buf.WriteString(`{"id":12345,"name":"item","active":true}`)
	}
	buf.WriteByte(']')

	res := &ExecuteResult{
		Status: http.StatusOK,
		Headers: http.Header{
			"Content-Type":        {"application/json"},
			"Jentic-Execution-Id": {"exec_bench"},
		},
		Body:        buf.Bytes(),
		ExecutionID: "exec_bench",
	}

	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		_, _ = res.EnvelopeWithin(capBytes)
	}
}
