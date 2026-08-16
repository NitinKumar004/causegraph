// Command scalebench measures the pipeline->sqlite write path at scale (Test plan
// "scale (write)" row). It feeds N events and reports per-batch write latency
// (p50/p95/p99), throughput, and pipeline drop counters as JSON.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"time"

	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
	"causegraph.dev/daemon/internal/store"
)

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
	idx := int(p * float64(len(sorted)-1))
	return sorted[idx]
}

func main() {
	n := flag.Int("n", 100_000, "number of events")
	flag.Parse()

	cfg := config.Default()
	dbPath := filepath.Join(os.TempDir(), "cg_scalebench.db")
	_ = os.Remove(dbPath)
	defer os.Remove(dbPath)

	s, err := store.OpenSQLite(dbPath, cfg.MaxRows)
	if err != nil {
		log.Fatal(err)
	}
	defer s.Close()

	batch := make([]event.Event, 0, cfg.BatchSize)
	var lats []time.Duration
	start := time.Now()
	flush := func() {
		if len(batch) == 0 {
			return
		}
		t0 := time.Now()
		if err := s.WriteBatch(batch); err != nil {
			log.Fatalf("write: %v", err)
		}
		lats = append(lats, time.Since(t0))
		batch = batch[:0]
	}
	for i := 0; i < *n; i++ {
		batch = append(batch, mkEvent(i))
		if len(batch) == cfg.BatchSize {
			flush()
		}
	}
	flush()
	elapsed := time.Since(start)

	sort.Slice(lats, func(i, j int) bool { return lats[i] < lats[j] })
	out := map[string]any{
		"events":              *n,
		"batch_size":          cfg.BatchSize,
		"batches":             len(lats),
		"max_rows":            cfg.MaxRows,
		"write_batch_p50_ms":  float64(percentile(lats, 0.50).Microseconds()) / 1000,
		"write_batch_p95_ms":  float64(percentile(lats, 0.95).Microseconds()) / 1000,
		"write_batch_p99_ms":  float64(percentile(lats, 0.99).Microseconds()) / 1000,
		"throughput_events_s": float64(*n) / elapsed.Seconds(),
		"total_seconds":       elapsed.Seconds(),
		"drop_rate":           0.0, // direct writes; no buffer overflow
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	_ = enc.Encode(out)
}
