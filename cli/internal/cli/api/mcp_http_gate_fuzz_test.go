package api

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
)

func FuzzAllPreAuthMethods(f *testing.F) {
	seeds := []string{
		`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping"}`,
		`{"jsonrpc":"2.0","method":"notifications/initialized"}`,
		`{"jsonrpc":"2.0","id":"a","method":"ping"}`,
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"execute"}}`,
		`{"jsonrpc":"2.0","id":1,"method":"resources/read"}`,
		`[{"jsonrpc":"2.0","id":1,"method":"tools/list"},{"jsonrpc":"2.0","id":2,"method":"ping"}]`,
		`[{"jsonrpc":"2.0","id":1,"method":"tools/list"},{"jsonrpc":"2.0","id":2,"method":"tools/call"}]`,
		`[]`,
		`not json`,
		`{"jsonrpc":"2.0","id":1}`,
		`{"jsonrpc":"2.0","method":"notifications\/initialized"}`,
		`{"jsonrpc":"2.0","id":1,"method":"\u0070ing"}`,
		`{"jsonrpc":"2.0","id":1,"method":"tools\/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping\u0000"}`,
		`{"jsonrpc":"2.0","id":1,"method":null}`,
		`{"jsonrpc":"2.0","id":1,"method":["ping"]}`,
		`{"jsonrpc":"2.0","id":1,"method":1}`,
		`{"id":1,"method":"ping"}`,
		`{"jsonrpc":"1.0","id":1,"method":"ping"}`,
		`{"jsonrpc":"2.0","id":true,"method":"ping"}`,
		`{"jsonrpc":"2.0","id":1,"Method":"ping","method":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","Method":"ping"}`,
		`{"jsonrpc":"2.0","id":1,"METHOD":"ping","method":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping","METHOD":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping","method":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","method":"ping"}`,
		`{"jsonrpc":"2.0","id":1,"METHOD":"ping"}`,
		`{"jsonrpc":"2.0","id":1,"\u006dethod":"ping","method":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping","params":{},"Params":{}}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping","param\u017f":{}}`,
		`{"jsonrpc":"2.0","JSONRPC":"2.0","id":1,"method":"ping"}`,
		`{"jsonrpc":"2.0","id":1,"Id":2,"method":"ping"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping","method\u0000":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping","m\u00e9thod":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping"}{"method":"tools/call"}`,
		`{"jsonrpc":"2.0","id":1,"method":"ping"} trailing`,
		" \t\r\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\",\"params\":{\"Method\":\"x\"}}\n",
		"\u00a0{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}",
		"\ufeff{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}",
		`[{"jsonrpc":"2.0","id":1,"method":"ping"},{"jsonrpc":"2.0","id":2,"Method":"ping","method":"tools/call"}]`,
		`[{"jsonrpc":"2.0","id":1,"method":"ping"},{"jsonrpc":"2.0","id":2,"METHOD":"tools/call","method":"ping"}]`,
		`[{"jsonrpc":"2.0","id":1,"method":"ping"}][{"jsonrpc":"2.0","id":2,"method":"tools/call"}]`,
		`[{"jsonrpc":"2.0","id":1,"method":"ping"},"x"]`,
		`[[{"jsonrpc":"2.0","id":1,"method":"ping"}]]`,
		`[{"jsonrpc":"2.0","id":1,"method":"ping"},{"jsonrpc":"2.0","id":1,"result":{}}]`,
	}
	for _, s := range seeds {
		f.Add([]byte(s))
	}

	f.Fuzz(func(t *testing.T, body []byte) {
		if !allPreAuthMethods(body) {
			return
		}

		trimmed := bytes.Trim(body, jsonWhitespace)
		if len(trimmed) == 0 {
			t.Fatalf("allPreAuthMethods(%q) = true on empty body", body)
		}

		assertWhitelistedRequest := func(raw []byte) {
			t.Helper()
			msg, err := jsonrpc.DecodeMessage(raw)
			if err != nil {
				t.Fatalf("allPreAuthMethods(%q) = true, but jsonrpc.DecodeMessage(%q) failed: %v", body, raw, err)
			}
			req, ok := msg.(*jsonrpc.Request)
			if !ok {
				t.Fatalf("allPreAuthMethods(%q) = true, but decoded message %T is not *jsonrpc.Request", body, msg)
			}
			if !mcpPreAuthMethods[req.Method] {
				t.Fatalf("allPreAuthMethods(%q) = true, but jsonrpc.DecodeMessage yielded non-whitelisted method %q", body, req.Method)
			}
		}

		if trimmed[0] != '[' {
			assertWhitelistedRequest(trimmed)
			return
		}

		var batch []json.RawMessage
		if err := json.Unmarshal(trimmed, &batch); err != nil || len(batch) == 0 {
			t.Fatalf("allPreAuthMethods(%q) = true for batch, but json.Unmarshal failed (len=%d, err=%v)", body, len(batch), err)
		}
		for _, elem := range batch {
			assertWhitelistedRequest(bytes.Trim(elem, jsonWhitespace))
		}
	})
}
