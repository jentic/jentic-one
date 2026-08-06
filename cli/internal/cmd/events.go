package cmd

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newEventsCmd is the `jentic events` root. Read-only; not fenced.
func newEventsCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "events",
		Short: "Observe control-plane events",
		Args:  cobra.NoArgs,
		RunE:  func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(newEventsWatchCmd(app))
	return cmd
}

type eventsWatchOptions struct {
	traceID     string
	eventTypes  []string
	lastEventID string
}

func newEventsWatchCmd(app *App) *cobra.Command {
	opts := &eventsWatchOptions{}
	cmd := &cobra.Command{
		Use:   "watch",
		Short: "Tail the control-plane event stream as NDJSON",
		Long: "watch connects to the control plane's Server-Sent Events stream and emits\n" +
			"one JSON event per line (NDJSON) until interrupted. Use --last-event-id to\n" +
			"resume after a previously seen event (the stream also advertises the id of\n" +
			"each event so a wrapper can persist it). Ctrl-C stops cleanly.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			return runEventsWatch(cmd.Context(), client, app.Out, opts)
		},
	}
	cmd.Flags().StringVar(&opts.traceID, "trace", "", "Only events for this trace id")
	cmd.Flags().StringArrayVar(&opts.eventTypes, "type", nil, "Only events of these types (repeatable)")
	cmd.Flags().StringVar(&opts.lastEventID, "last-event-id", "", "Resume after this event id")
	return cmd
}

// runEventsWatch opens the SSE stream and forwards each event as an NDJSON line.
// It returns nil on a clean context cancellation (Ctrl-C) — a deliberately-stopped
// tail is success, not failure.
func runEventsWatch(ctx context.Context, client *control.ClientWithResponses, out io.Writer, opts *eventsWatchOptions) error {
	params := &control.StreamEventsParams{}
	if opts.traceID != "" {
		params.TraceId = &opts.traceID
	}
	if len(opts.eventTypes) > 0 {
		types := opts.eventTypes
		params.EventType = &types
	}
	if opts.lastEventID != "" {
		params.LastEventID = &opts.lastEventID
	}

	resp, err := client.StreamEvents(ctx, params)
	if err != nil {
		if errors.Is(err, context.Canceled) {
			return nil
		}
		return err
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		return &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg:  fmt.Sprintf("event stream returned status %d", resp.StatusCode),
		}
	}

	err = forwardSSE(ctx, resp.Body, out)
	if errors.Is(err, context.Canceled) || errors.Is(err, io.EOF) {
		return nil
	}
	return err
}

// forwardSSE parses a Server-Sent Events body and writes each event's `data`
// payload as one redacted NDJSON line. It follows the SSE framing: lines starting
// with `data:` accumulate the payload, a blank line dispatches the event, and
// `id:`/`event:` lines are metadata (id is echoed into the emitted object so a
// consumer can persist it for --last-event-id resume). Comment lines (`:`) and the
// periodic keep-alive are ignored.
func forwardSSE(ctx context.Context, body io.Reader, out io.Writer) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	var dataLines []string
	var eventID string

	flush := func() error {
		if len(dataLines) == 0 {
			return nil
		}
		payload := strings.Join(dataLines, "\n")
		dataLines = dataLines[:0]
		id := eventID
		eventID = ""

		// Decode into a generic object so the redaction funnel can see field names;
		// a payload that is not valid JSON is forwarded as a raw string field rather
		// than dropped, so an operator still sees malformed events.
		var obj map[string]any
		if json.Unmarshal([]byte(payload), &obj) != nil {
			obj = map[string]any{"raw": payload}
		}
		if id != "" {
			obj["_event_id"] = id
		}
		return ux.WriteJSONLine(out, obj)
	}

	for scanner.Scan() {
		if err := ctx.Err(); err != nil {
			return err
		}
		line := scanner.Text()
		switch {
		case line == "":
			if err := flush(); err != nil {
				return err
			}
		case strings.HasPrefix(line, ":"):
			// SSE comment / keep-alive — ignore.
		case strings.HasPrefix(line, "data:"):
			dataLines = append(dataLines, strings.TrimPrefix(strings.TrimPrefix(line, "data:"), " "))
		case strings.HasPrefix(line, "id:"):
			eventID = strings.TrimSpace(strings.TrimPrefix(line, "id:"))
		default:
			// event:/retry:/unknown fields — not needed for the NDJSON tail.
		}
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	// Dispatch any trailing event with no terminating blank line.
	return flush()
}
