// Package store defines the Sink seam (architecture.md §5.2/§6). The pipeline
// writes through Sink; SQLite is the default implementation now, and a remote
// fleet sink later is one more implementation — not a rewrite.
package store

import "causegraph.dev/daemon/internal/event"

// Sink is the swappable store boundary. WriteBatch persists a batch atomically;
// on error the caller (pipeline) counts the batch as dropped and moves on — Sink
// implementations must not block indefinitely.
type Sink interface {
	WriteBatch(events []event.Event) error
	Close() error
}
