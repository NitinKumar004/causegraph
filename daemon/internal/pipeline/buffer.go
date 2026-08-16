// Package pipeline buffers, batches and drains events to the store without ever
// blocking the collector. Under pressure it drops the OLDEST event, never the
// newest — losing stale history beats stalling live capture (architecture.md §5.1).
package pipeline

import (
	"sync"
	"sync/atomic"

	"causegraph.dev/daemon/internal/event"
)

// Buffer is a bounded, drop-oldest ring buffer. Push never blocks; when full it
// evicts the oldest element and counts it. A Go channel drops the newest under
// pressure, which is the wrong end — hence a ring buffer, not a channel.
type Buffer struct {
	mu   sync.Mutex
	buf  []event.Event
	head int // index of oldest element
	size int
	cap  int

	droppedOverflow atomic.Int64
}

func NewBuffer(capacity int) *Buffer {
	if capacity < 1 {
		capacity = 1
	}
	return &Buffer{buf: make([]event.Event, capacity), cap: capacity}
}

// Push appends e. If the buffer is full it evicts the oldest element and
// increments the overflow counter. Never blocks.
func (b *Buffer) Push(e event.Event) {
	b.mu.Lock()
	if b.size == b.cap {
		b.buf[b.head] = e                // overwrite oldest
		b.head = (b.head + 1) % b.cap    // advance past it
		b.mu.Unlock()
		b.droppedOverflow.Add(1)
		return
	}
	b.buf[(b.head+b.size)%b.cap] = e
	b.size++
	b.mu.Unlock()
}

// PopBatch removes and returns up to max oldest elements (FIFO).
func (b *Buffer) PopBatch(max int) []event.Event {
	if max < 1 {
		return nil
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	n := b.size
	if n > max {
		n = max
	}
	if n == 0 {
		return nil
	}
	out := make([]event.Event, n)
	for i := 0; i < n; i++ {
		out[i] = b.buf[(b.head+i)%b.cap]
	}
	b.head = (b.head + n) % b.cap
	b.size -= n
	return out
}

func (b *Buffer) Len() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.size
}

func (b *Buffer) DroppedOverflow() int64 { return b.droppedOverflow.Load() }
