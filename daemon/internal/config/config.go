// Package config holds the daemon's runtime knobs with safe defaults. Every knob
// the plan names lives here so behaviour is inspectable and testable, not hidden
// behind literals.
package config

import "time"

type Config struct {
	// Store
	DBPath  string // SQLite file path
	MaxRows int64  // ring-buffer cap on the events table (evict-oldest past this)

	// Pipeline
	BufferSize    int           // in-memory bounded buffer; full => drop OLDEST
	BatchSize     int           // max events per store transaction
	BatchInterval time.Duration // flush timer even if BatchSize not reached

	// Collector (generic poll)
	SampleInterval    time.Duration // one tick: snapshot diff + resource sampling
	SampleMinCPUPct   float64       // "processes that matter": sample if cpu >= this
	SampleMinRSS      int64         // ...or rss_bytes >= this
	HeartbeatInterval time.Duration // periodic liveness event

	// Identity
	HostID string
}

// Default returns the plan's documented defaults.
func Default() Config {
	return Config{
		DBPath:            "causegraph.db",
		MaxRows:           1_000_000,
		BufferSize:        8192,
		BatchSize:         512,
		BatchInterval:     500 * time.Millisecond,
		SampleInterval:    2 * time.Second,
		SampleMinCPUPct:   1.0,
		SampleMinRSS:      100 << 20, // 100 MiB
		HeartbeatInterval: 30 * time.Second,
		HostID:            "",
	}
}
