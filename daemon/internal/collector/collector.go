// Package collector defines the capture seam: one interface, many backends
// (architecture.md §3.1). It is a leaf package (interface + Caps only) so any
// backend can import it without a cycle. M0–M2 ship only the generic poll
// backend; a native backend (M3: eBPF/ETW/ES) is one new file satisfying
// Collector — nothing downstream changes. Backend selection is wired in
// cmd/cged/main.go through this interface (M3 adds build-tag selection there).
package collector

import (
	"context"

	"causegraph.dev/daemon/internal/event"
)

// Fidelity distinguishes lower-fidelity polling from native kernel capture, so
// the engine (and edge scoring, later) knows what to trust.
type Fidelity string

const (
	FidelityPolling Fidelity = "polling"
	FidelityNative  Fidelity = "native"
)

// Caps lets the engine know what a backend can observe.
type Caps struct {
	ProcessSpawn  bool
	ProcessExit   bool
	FileEvents    bool
	SocketEvents  bool
	ResourceStats bool
	Fidelity      Fidelity
}

// Collector streams canonical events until the context is cancelled. Every
// backend touches nothing shared except the Event type it emits.
type Collector interface {
	Start(ctx context.Context, out chan<- event.Event) error
	Capabilities() Caps
	Close() error
}
