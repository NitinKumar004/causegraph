package main

import (
	"context"
	"io"
	"log"
	"os"
	"path/filepath"
	"testing"
	"time"

	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
	"causegraph.dev/daemon/internal/store"
)

// AC6: the heartbeat carries cged's own identity and is a valid canonical event.
func TestHeartbeatEventFields(t *testing.T) {
	cfg := config.Default()
	cfg.HostID = "testhost"
	e := heartbeatEvent(cfg)
	if e.Kind != event.KindHeartbeat {
		t.Errorf("kind = %q, want heartbeat", e.Kind)
	}
	if e.Actor.Pid != int64(os.Getpid()) {
		t.Errorf("heartbeat pid = %d, want own pid %d", e.Actor.Pid, os.Getpid())
	}
	if e.Actor.Ppid != int64(os.Getppid()) {
		t.Errorf("heartbeat ppid = %d, want own ppid %d", e.Actor.Ppid, os.Getppid())
	}
	if len(e.Actor.Args) != 0 {
		t.Errorf("heartbeat args should be empty, got %v", e.Actor.Args)
	}
	if e.Source != event.SourcePoll || e.Confidence != 1.0 || e.HostID != "testhost" {
		t.Errorf("heartbeat provenance wrong: %+v", e)
	}
	if err := e.Validate(); err != nil {
		t.Errorf("heartbeat invalid: %v", err)
	}
}

// AC6: the full daemon lifecycle — run wires collector->pipeline->store, emits
// heartbeats, writes real events, and shuts down cleanly when ctx is cancelled
// (a hang in the shutdown ordering fails this via the 5s guard).
func TestRunEndToEndCleanShutdown(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "cged.db")
	cfg := config.Default()
	cfg.DBPath = dbPath
	cfg.SampleInterval = 50 * time.Millisecond
	cfg.HeartbeatInterval = 60 * time.Millisecond
	cfg.HostID = "testhost"

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	done := make(chan error, 1)
	go func() { done <- run(ctx, cfg, log.New(io.Discard, "", 0)) }()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("run returned error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("run did not shut down within 5s — deadlock in shutdown ordering")
	}

	// The daemon-written DB must be readable and contain heartbeats + real events.
	s, err := store.OpenSQLite(dbPath, cfg.MaxRows)
	if err != nil {
		t.Fatalf("reopen daemon DB: %v", err)
	}
	defer s.Close()
	total, _ := s.Count()
	if total == 0 {
		t.Fatal("daemon wrote no events")
	}
	var hb int64
	if err := s.DB().QueryRow(`SELECT COUNT(*) FROM events WHERE kind='heartbeat'`).Scan(&hb); err != nil {
		t.Fatal(err)
	}
	if hb < 2 {
		t.Errorf("expected >=2 heartbeats over 500ms @60ms, got %d", hb)
	}
	if v, _ := s.MetaSchemaVersion(); v == "" {
		t.Error("daemon DB missing meta schema_version")
	}
}

// AC3: both collectors (poller + filewatch) run into one channel; a file change
// during the run is captured. Also proves the WaitGroup fan-in (no double-close panic).
func TestRunWithFileWatchCapturesFileChange(t *testing.T) {
	tmp := t.TempDir()
	dbPath := filepath.Join(tmp, "cged.db")
	watchDir := filepath.Join(tmp, "watched")
	if err := os.MkdirAll(watchDir, 0o755); err != nil {
		t.Fatal(err)
	}
	cfg := config.Default()
	cfg.DBPath = dbPath
	cfg.SampleInterval = 50 * time.Millisecond
	cfg.HeartbeatInterval = 60 * time.Millisecond
	cfg.WatchPaths = []string{watchDir}
	cfg.HostID = "h"

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- run(ctx, cfg, log.New(io.Discard, "", 0)) }()

	time.Sleep(250 * time.Millisecond) // let the watcher register
	if err := os.WriteFile(filepath.Join(watchDir, "app.conf"), []byte("k=v"), 0o644); err != nil {
		t.Fatal(err)
	}
	time.Sleep(400 * time.Millisecond)
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("run error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("run did not shut down (double-close/deadlock?)")
	}

	s, err := store.OpenSQLite(dbPath, cfg.MaxRows)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	var fc int64
	if err := s.DB().QueryRow(`SELECT COUNT(*) FROM events WHERE kind='file.change'`).Scan(&fc); err != nil {
		t.Fatal(err)
	}
	if fc == 0 {
		t.Error("expected at least one file.change event from the watcher")
	}
}
