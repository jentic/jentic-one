package api

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func testListBrowser(items ...string) *listBrowser[string] {
	return &listBrowser[string]{items: items, listWidth: 20, width: 90, height: 24}
}

func testFrame(l *listBrowser[string]) listFrame {
	return listFrame{
		title: "Things", header: "n things", filter: "all", empty: "nothing here",
		hint: "HINT", footer: "FOOTER",
		row:    func(i int) string { return l.listRow(i, i == 0, l.items[i]) },
		detail: func() string { item, _ := l.current(); return "DETAIL " + item },
	}
}

func TestListBrowser_ApplyPage(t *testing.T) {
	l := testListBrowser("a", "b")
	l.cursor, l.top, l.loading, l.err = 1, 1, true, "boom"

	l.applyPage([]string{"c"}, false, true, "next")
	if strings.Join(l.items, ",") != "a,b,c" || l.cursor != 1 || !l.hasMore || l.nextCursor != "next" {
		t.Errorf("append: items=%v cursor=%d hasMore=%v next=%q", l.items, l.cursor, l.hasMore, l.nextCursor)
	}
	if l.loading || l.err != "" {
		t.Errorf("append: loading=%v err=%q, want both cleared", l.loading, l.err)
	}

	l.applyPage([]string{"z"}, true, true, "")
	if strings.Join(l.items, ",") != "z" || l.cursor != 0 || l.top != 0 {
		t.Errorf("reset: items=%v cursor=%d top=%d", l.items, l.cursor, l.top)
	}
	if l.hasMore {
		t.Error("reset: hasMore without a next cursor must read as the last page")
	}
}

func TestListBrowser_OnListKeyMovesAndPrefetches(t *testing.T) {
	l := testListBrowser("a", "b", "c", "d")
	l.hasMore = true
	loads := 0
	loadMore := func() tea.Cmd { loads++; return func() tea.Msg { return nil } }

	if _, ok := l.onListKey(keyMsg("k"), loadMore); !ok || l.cursor != 0 {
		t.Fatalf("up at the top: ok=%v cursor=%d", ok, l.cursor)
	}
	// Moving into the last three rows prefetches the next page once.
	if cmd, ok := l.onListKey(keyMsg("j"), loadMore); !ok || cmd == nil || l.cursor != 1 || !l.loading {
		t.Fatalf("down: ok=%v cmd=%v cursor=%d loading=%v, want a prefetch", ok, cmd != nil, l.cursor, l.loading)
	}
	if cmd, _ := l.onListKey(keyMsg("j"), loadMore); cmd != nil || loads != 1 {
		t.Errorf("down while loading: cmd=%v loads=%d, want no second prefetch", cmd != nil, loads)
	}

	l.query = "pay"
	if _, ok := l.onListKey(keyMsg("/"), loadMore); !ok || !l.searching || l.searchInput != "pay" {
		t.Errorf("/: searching=%v input=%q, want the search seeded with the query", l.searching, l.searchInput)
	}
	if _, ok := l.onListKey(keyMsg("x"), loadMore); ok {
		t.Error("an unshared key must fall through to the browser")
	}
	if cmd, ok := l.onListKey(keyMsg("q"), loadMore); !ok || cmd == nil || !l.done {
		t.Errorf("q: ok=%v done=%v, want quit", ok, l.done)
	}
}

func TestListBrowser_OnSearchKey(t *testing.T) {
	l := testListBrowser()
	l.searching = true
	reloads := 0
	reload := func() tea.Cmd { reloads++; return func() tea.Msg { return nil } }

	for _, k := range []string{"s", "t", "r", "x"} {
		l.onSearchKey(keyMsg(k), reload)
	}
	l.onSearchKey(tea.KeyMsg{Type: tea.KeyBackspace}, reload)
	if l.searchInput != "str" {
		t.Fatalf("input = %q, want str", l.searchInput)
	}
	l.searchInput = "  str  "
	if cmd := l.onSearchKey(tea.KeyMsg{Type: tea.KeyEnter}, reload); cmd == nil || reloads != 1 {
		t.Fatalf("enter: cmd=%v reloads=%d, want one reload", cmd != nil, reloads)
	}
	if l.searching || l.query != "str" || !l.loading {
		t.Errorf("enter: searching=%v query=%q loading=%v", l.searching, l.query, l.loading)
	}

	l.searching = true
	l.onSearchKey(tea.KeyMsg{Type: tea.KeyEsc}, reload)
	if l.searching || reloads != 1 {
		t.Errorf("esc: searching=%v reloads=%d, want a cancel without a reload", l.searching, reloads)
	}
}

func TestListBrowser_ViewFrame(t *testing.T) {
	cases := []struct {
		name    string
		setup   func(l *listBrowser[string])
		want    []string
		notWant []string
	}{
		{"error", func(l *listBrowser[string]) { l.err = "it broke" }, []string{"it broke", "HINT"}, []string{"FOOTER", "DETAIL"}},
		{"loading", func(l *listBrowser[string]) { l.items, l.loading = nil, true }, []string{"loading …", "HINT"}, []string{"FOOTER"}},
		{"empty", func(l *listBrowser[string]) { l.items = nil }, []string{"nothing here", "HINT"}, []string{"FOOTER"}},
		{"list", func(*listBrowser[string]) {}, []string{"Things", "n things", "alpha", "beta", "DETAIL alpha", "FOOTER"}, []string{"HINT"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			l := testListBrowser("alpha", "beta")
			tc.setup(l)
			got := l.view(testFrame(l))
			for _, w := range tc.want {
				if !strings.Contains(got, w) {
					t.Errorf("view missing %q:\n%s", w, got)
				}
			}
			for _, w := range tc.notWant {
				if strings.Contains(got, w) {
					t.Errorf("view carries %q:\n%s", w, got)
				}
			}
		})
	}
	l := testListBrowser("alpha")
	l.done = true
	if got := l.view(testFrame(l)); got != "" {
		t.Errorf("done view = %q, want empty", got)
	}
}

// TestBrowsers_RenderThroughTheSharedFrame pins both TUIs to listBrowser: a
// loaded list renders the shared columns with each browser's own rows,
// detail and hints.
func TestBrowsers_RenderThroughTheSharedFrame(t *testing.T) {
	cat := &catalogBrowser{
		listBrowser: listBrowser[catalogEntry]{
			items: []catalogEntry{{APIID: "stripe.com", Registered: true}}, listWidth: catalogListColumn, width: 90, height: 24,
		},
		previews: map[string]*catalogPreview{}, previewErr: map[string]string{},
	}
	if got := cat.View(); !strings.Contains(got, "Catalog") || !strings.Contains(got, "stripe.com") ||
		!strings.Contains(got, "i import") {
		t.Errorf("catalog view:\n%s", got)
	}

	apis := &apisBrowser{
		listBrowser: listBrowser[registeredAPI]{
			items:     []registeredAPI{{API: apiRef{Vendor: "acme", Name: "pay", Version: "1"}, CurrentRevisionID: "rev_1"}},
			listWidth: apisListColumn, width: 90, height: 24,
		},
		ops: map[string]*operationListResult{}, opsErr: map[string]string{},
	}
	if got := apis.View(); !strings.Contains(got, "APIs") || !strings.Contains(got, "live") ||
		!strings.Contains(got, "enter revisions") {
		t.Errorf("apis view:\n%s", got)
	}
}
