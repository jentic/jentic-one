package api

import (
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// The execute tools' input schema. Permissive like every 1-A/1-B schema (no
// additionalProperties:false, no alias properties — the normalizer resolves
// them handler-side); the declared shapes are the canonical spellings.
func executeInputSchema(withBody bool) map[string]any {
	props := map[string]any{
		"operation_id": map[string]any{
			"type": "string",
			"description": "The operation to execute (required; \"id\" and \"uuid\" are accepted aliases): a METHOD:url " +
				"pair like \"GET:https://api.example.com/v1/things\" — pass a search_apis hit's target verbatim. " +
				"(A registry operation id also resolves, for compatibility — prefer METHOD:url.)",
		},
		"inputs": map[string]any{
			"type": "object",
			"description": "Operation parameters as one flat object (\"params\"/\"parameters\" are accepted aliases): " +
				"keys matching a {placeholder} in the operation's path fill it; every other key is sent as a query parameter.",
		},
		"headers": map[string]any{
			"type":        "object",
			"description": "Extra request headers as a string-valued object.",
		},
		"revision": map[string]any{
			"type":        "string",
			"description": "Pin a specific revision ID for reproducibility (defaults to the current revision).",
		},
	}
	if withBody {
		props["body"] = map[string]any{
			"description": "The request body as a JSON value (object, array, or string; \"data\" is an accepted alias). " +
				"Omit it for bodyless calls — the body is ALWAYS this argument, never read from anywhere else. " +
				"A string whose content parses as JSON is deliberately treated as a stringified body and sent " +
				"as that JSON value, not as a quoted string.",
		}
		props["idempotency_key"] = map[string]any{
			"type": "string",
			"description": "Optional Idempotency-Key so a retried POST/PUT is de-duplicated by the broker instead of " +
				"running twice.",
		}
	}
	return map[string]any{"type": "object", "properties": props}
}

var getExecutionResultSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"held_execution_id": map[string]any{
			"type": "string",
			"description": "The held execution to poll (\"held_execution\" is an accepted alias): the " +
				"held_execution_id of a held (202) execute response. Pass exactly one of held_execution_id and job_id.",
		},
		"job_id": map[string]any{
			"type": "string",
			"description": "The job to poll (\"id\" and \"job\" are accepted aliases): the job_id of an " +
				"asynchronous (202) execute response that is not held. Pass exactly one of held_execution_id and job_id.",
		},
		"timeout": map[string]any{
			"type":    "integer",
			"minimum": 0,
			"maximum": maxPollTimeoutSeconds,
			"default": defaultPollTimeoutSeconds,
			"description": "Optional. Wait up to this many seconds (default and max 30; larger values are " +
				"capped at 30) for the call to finish, re-polling and returning as soon as it has. " +
				"0 returns the current status at once.",
		},
	},
}

// executeToolSpecs declares the PR 1-C tool surface. Annotations per master
// §3.2: execute is the ONLY tool carrying destructiveHint (openWorldHint on
// both execute variants — they reach arbitrary upstream APIs); execute_read
// and get_execution_result are read-only. --read-only therefore withholds
// exactly `execute`.
func (s *mcpServer) executeToolSpecs() []mcpToolSpec {
	openWorld := true
	destructive := true
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "execute",
				Title: "Execute an API operation",
				Description: "Execute an operation through the Jentic broker: the broker authenticates this agent " +
					"and injects the stored upstream credential server-side — credentials never pass through " +
					"this session. This is the final step of the flow (whoami → search_apis → " +
					"inspect_operation → execute): always inspect the contract first, and never execute just " +
					"to probe whether you have access (call whoami). " +
					`Example: {"operation_id": "POST:https://api.example.com/v1/pets", "inputs": {"petId": "42", "limit": 10}, ` +
					`"body": {"name": "Bob"}}. ` +
					"Returns {status, headers, body, execution_id}: any HTTP status, including upstream " +
					"4xx/5xx, is the upstream's answer — a denial by the broker itself comes back as an " +
					"error result with recovery directions instead. A 202 response with status \"held\" means " +
					"the call is HELD for human approval: above all, show the user its review_url (a reviewer " +
					"signs in there to approve or deny it) and end your turn so they see it; once the user " +
					"replies, call get_execution_result once with the held_execution_id it carries (it waits up to " +
					"30 seconds by default) — never re-send the execute. Large bodies are truncated " +
					"({truncated: true, total_bytes}); narrow the call (query parameters, pagination) to see " +
					"the rest. Prefer execute_read for pure reads — it is approved more readily.",
				InputSchema: executeInputSchema(true),
				Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &openWorld},
			},
			handler: s.handleExecute,
		},
		{
			tool: &mcp.Tool{
				Name:  "execute_read",
				Title: "Execute a read-only API operation",
				Description: "Execute a GET or HEAD operation through the Jentic broker — the read-only variant of " +
					"execute (same envelope, same flow, no request body). Use it for every pure read: clients " +
					"approve read-only tools more readily. If the operation resolves to any other HTTP " +
					"method, the call is rejected — use execute. " +
					`Example: {"operation_id": "GET:https://rest.coincap.io/v3/markets", "inputs": {"limit": 5}}.`,
				InputSchema: executeInputSchema(false),
				Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, OpenWorldHint: &openWorld},
			},
			handler: s.handleExecuteRead,
		},
		{
			tool: &mcp.Tool{
				Name:  "get_execution_result",
				Title: "Poll a held or asynchronous execution",
				Description: "Poll a held or asynchronous execution, waiting up to timeout seconds (default and " +
					"max 30; 0 answers at once) and returning as soon as the call has finished. Pass " +
					"held_execution_id for a 202 HELD response (human approval required): returns " +
					"{held_execution_id, state, status, review_url, expires_at, ...} — state is the decision " +
					"(pending, approved, denied, expired, withdrawn), status where the call stands (held, queued, " +
					"running, completed, failed, cancelled) — and, once the call has finished, the result: the " +
					"upstream response, or the problem explaining why it never ran (denied or expired). While the " +
					"call has not finished the payload carries an agent_directive: show the user the review_url " +
					"again and end your turn. Above all, show the user the review_url from the held response and " +
					"end your turn so they see it; only after the user replies, call this once with the " +
					"held_execution_id. Pass job_id instead for an asynchronous 202 that is not held: returns " +
					"{job_id, kind, status, ...} and, once \"completed\", the result document. NEVER re-send the " +
					"execute while a call is pending; approval happens out-of-band and re-sending duplicates the " +
					`call. Example: {"held_execution_id": "held_abc123"}.`,
				InputSchema: getExecutionResultSchema,
				Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
			},
			handler: s.handleGetExecutionResult,
		},
	}
}
