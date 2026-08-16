package pipeline

import (
	"context"
	"errors"
	"io"
	"log"
	"sync"
	"testing"
	"time"

	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
)

type fakeSink struct {
	mu      sync.Mutex
	written []event.Event
	failAll bool
}

func (f *fakeSink) WriteBatch(evs []event.Event) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failAll {
		return errors.New("disk on fire")
	}
	f.written = append(f.written, evs...)
	return nil
}
func (f *fakeSink) Close() error { return nil }
func (f *fakeSink) count() int   { f.mu.Lock(); defer f.mu.Unlock(); return len(f.written) }

func testCfg() config.Config {
	c := config.Default()
	c.BufferSize = 64
	c.BatchSize = 8
	c.BatchInterval = 5 * time.Millisecond
	return c
}

func quietLogger() *log.Logger { return log.New(io.Discard, "", 0) }

// AC: pipeline drains buffered events to the sink.
func TestDrainWritesToSink(t *testing.T) {
	sink := &fakeSink{}
	p := New(testCfg(), sink, quietLogger())
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { p.Run(ctx); close(done) }()

	for i := int64(0); i < 20; i++ {
		p.Ingest(ev(i))
	}
	// Wait until drained or timeout.
	deadline := time.After(2 * time.Second)
	for sink.count() < 20 {
		select {
		case <-deadline:
			t.Fatalf("only %d/20 written", sink.count())
		case <-time.After(2 * time.Millisecond):
		}
	}
	cancel()
	<-done
}

// AC8: on a store write error the pipeline does not retry — it counts the batch
// into dropped_write_error and keeps draining. No panic.
func TestStoreWriteErrorCounted(t *testing.T) {
	sink := &fakeSink{failAll: true}
	p := New(testCfg(), sink, quietLogger())
	for i := int64(0); i < 20; i++ {
		p.Ingest(ev(i))
	}
	p.drainAll() // deterministic single drain
	if got := p.DroppedWriteError(); got != 20 {
		t.Errorf("dropped_write_error=%d, want 20", got)
	}
	if p.DroppedOverflow() != 0 {
		t.Errorf("overflow should be 0, got %d", p.DroppedOverflow())
	}
	if sink.count() != 0 {
		t.Errorf("nothing should be written to a failing sink, got %d", sink.count())
	}
}

// AC: SIGINT semantics — cancelling ctx triggers a final flush before Run returns.
func TestCtxCancelFinalFlush(t *testing.T) {
	sink := &fakeSink{}
	cfg := testCfg()
	cfg.BatchInterval = time.Hour // ensure only the cancel-path flush runs
	p := New(cfg, sink, quietLogger())
	for i := int64(0); i < 10; i++ {
		p.Ingest(ev(i))
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { p.Run(ctx); close(done) }()
	cancel()
	<-done
	if sink.count() != 10 {
		t.Errorf("final flush wrote %d, want 10", sink.count())
	}
}

// AC4: the collector never blocks on a stalled store — Ingest returns promptly and
// the bounded buffer drops oldest instead of growing without bound.
func TestCollectorNeverBlocks(t *testing.T) {
	sink := &fakeSink{failAll: true} // store effectively stalled
	cfg := testCfg()
	cfg.BufferSize = 100
	p := New(cfg, sink, quietLogger())

	start := time.Now()
	const n = 100_000
	for i := int64(0); i < n; i++ {
		p.Ingest(ev(i)) // must not block
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("ingest of %d took %v — collector appears to block", n, elapsed)
	}
	if p.Buffered() > cfg.BufferSize {
		t.Errorf("buffer grew past cap: %d > %d", p.Buffered(), cfg.BufferSize)
	}
	if p.DroppedOverflow() == 0 {
		t.Errorf("expected overflow drops under a stalled store")
	}
	if p.DroppedOverflow()+int64(p.Buffered()) != n {
		t.Errorf("conservation: dropped=%d + buffered=%d != %d",
			p.DroppedOverflow(), p.Buffered(), n)
	}
}
