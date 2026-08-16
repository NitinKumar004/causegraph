// Command scalebench measures the real pipeline->sqlite write path at scale (Test
// plan "scale (write)" row). It feeds N events through pipeline.Ingest/Run (the
// actual drop-oldest buffer + batched drain), timing each store WriteBatch from
// inside the pipeline via a wrapping Sink, and reports p50/p95/p99 write-batch
// latency, throughput, and the MEASURED drop counters. A second scenario drives a
// deliberately slow sink with a small buffer to show drop-oldest actually fires.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
	"causegraph.dev/daemon/internal/pipeline"
	"causegraph.dev/daemon/internal/store"
)

// timingSink wraps a real Sink and records each WriteBatch latency + count, so we
// measure the write path the pipeline actually drives (not a direct-call bypass).
type timingSink struct {
	inner   store.Sink
	mu      sync.Mutex
	lats    []time.Duration
	written int
	delay   time.Duration // optional artificial slowness (drop-demo scenario)
}

func (t *timingSink) WriteBatch(evs []event.Event) error {
	if t.delay > 0 {
		time.Sleep(t.delay)
	}
	start := time.Now()
	err := t.inner.WriteBatch(evs)
	t.mu.Lock()
	t.lats = append(t.lats, time.Since(start))
	if err == nil {
		t.written += len(evs)
	}
	t.mu.Unlock()
	return err
}
func (t *timingSink) Close() error { return t.inner.Close() }
func (t *timingSink) count() int   { t.mu.Lock(); defer t.mu.Unlock(); return t.written }

func mkEvent(i int) event.Event {
	return event.Event{
		ID: fmt.Sprintf("e%d", i), Ts: int64(i), HostID: "bench",
		Kind:  event.KindProcessSpawn,
		Actor: event.Actor{Pid: int64(i), Ppid: int64(i / 10), Exe: "/bin/x", Args: []string{"x"}, User: "u"},
		Source: event.SourcePoll, Confidence: 1.0,
	}
}

func percentile(sorted []time.Duration, p float64) time.Duration {
	if len(sorted) == 0 {
		return 0
	}
	return sorted[int(p*float64(len(sorted)-1))]
}

func newStore(name string, maxRows int64) *store.SQLite {
	p := filepath.Join(os.TempDir(), name)
	_ = os.Remove(p)
	s, err := store.OpenSQLite(p, maxRows)
	if err != nil {
		log.Fatal(err)
	}
	return s
}

func main() {
	n := flag.Int("n", 100_000, "number of events")
	flag.Parse()

	// Scenario A — gate: drop==0 when BufferSize >= inflight. Buffer holds all N.
	cfgA := config.Default()
	cfgA.BufferSize = *n * 2
	cfgA.BatchInterval = time.Millisecond
	ts := &timingSink{inner: newStore("cg_scale_a.db", cfgA.MaxRows)}
	defer ts.Close()
	p := pipeline.New(cfgA, ts, log.New(os.Stderr, "", 0))

	ctx, cancel := context.WithCancel(context.Background())
	pipeDone := make(chan struct{})
	go func() { _ = p.Run(ctx); close(pipeDone) }()

	start := time.Now()
	for i := 0; i < *n; i++ {
		p.Ingest(mkEvent(i))
	}
	// Wait until every event has been persisted (or a generous timeout).
	deadline := time.Now().Add(60 * time.Second)
	for ts.count() < *n && time.Now().Before(deadline) {
		time.Sleep(2 * time.Millisecond)
	}
	elapsed := time.Since(start)
	cancel()
	<-pipeDone

	ts.mu.Lock()
	lats := append([]time.Duration(nil), ts.lats...)
	ts.mu.Unlock()
	sort.Slice(lats, func(i, j int) bool { return lats[i] < lats[j] })

	dropped := p.DroppedOverflow() + p.DroppedWriteError()
	write := map[string]any{
		"events":               *n,
		"persisted":            ts.count(),
		"buffer_size":          cfgA.BufferSize,
		"batch_size":           cfgA.BatchSize,
		"batches":              len(lats),
		"write_batch_p50_ms":   float64(percentile(lats, 0.50).Microseconds()) / 1000,
		"write_batch_p95_ms":   float64(percentile(lats, 0.95).Microseconds()) / 1000,
		"write_batch_p99_ms":   float64(percentile(lats, 0.99).Microseconds()) / 1000,
		"throughput_events_s":  float64(*n) / elapsed.Seconds(),
		"total_seconds":        elapsed.Seconds(),
		"dropped_overflow":     p.DroppedOverflow(),
		"dropped_write_error":  p.DroppedWriteError(),
		"drop_rate":            float64(dropped) / float64(*n),
	}

	// Scenario B — small buffer + slow sink: drop-oldest MUST fire under pressure.
	cfgB := config.Default()
	cfgB.BufferSize = 1000
	cfgB.BatchInterval = 5 * time.Millisecond
	tsB := &timingSink{inner: newStore("cg_scale_b.db", cfgB.MaxRows), delay: 2 * time.Millisecond}
	defer tsB.Close()
	pB := pipeline.New(cfgB, tsB, log.New(os.Stderr, "", 0))
	ctxB, cancelB := context.WithCancel(context.Background())
	pbDone := make(chan struct{})
	go func() { _ = pB.Run(ctxB); close(pbDone) }()
	for i := 0; i < *n; i++ {
		pB.Ingest(mkEvent(i)) // faster than a slow sink can drain -> overflow drops
	}
	cancelB()
	<-pbDone
	dropDemo := map[string]any{
		"buffer_size":      cfgB.BufferSize,
		"slow_sink_ms":     2,
		"ingested":         *n,
		"dropped_overflow": pB.DroppedOverflow(),
		"note":             "small buffer + slow sink: oldest dropped, newest kept, daemon never blocked",
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	_ = enc.Encode(map[string]any{"scale_write": write, "drop_oldest_demo": dropDemo})
}
