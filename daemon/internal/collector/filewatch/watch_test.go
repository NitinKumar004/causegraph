package filewatch

import (
	"context"
	"io"
	"log"
	"os"
	"path/filepath"
	"testing"
	"time"

	"causegraph.dev/daemon/internal/collector"
	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
)

var _ collector.Collector = (*Watcher)(nil)

func quiet() *log.Logger { return log.New(io.Discard, "", 0) }

func TestCapabilities(t *testing.T) {
	w, _ := New(config.Default(), quiet())
	c := w.Capabilities()
	if !c.FileEvents || c.ProcessSpawn || c.ResourceStats {
		t.Errorf("filewatch caps wrong: %+v", c)
	}
}

// AC1: watching a dir emits a valid file.change event with target.path + sentinel actor.
func TestStartEmitsFileChange(t *testing.T) {
	dir := t.TempDir()
	cfg := config.Default()
	cfg.WatchPaths = []string{dir}
	cfg.HostID = "h"
	w, _ := New(cfg, quiet())

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out := make(chan event.Event, 64)
	go w.Start(ctx, out)

	time.Sleep(150 * time.Millisecond) // let the watcher register
	target := filepath.Join(dir, "app.conf")
	if err := os.WriteFile(target, []byte("x=1"), 0o644); err != nil {
		t.Fatal(err)
	}

	select {
	case e := <-out:
		if e.Kind != event.KindFileChange {
			t.Fatalf("kind = %q, want file.change", e.Kind)
		}
		if e.Source != event.SourceFsnotify {
			t.Errorf("source = %q, want fsnotify", e.Source)
		}
		if e.Target == nil || e.Target.Path == nil || filepath.Base(*e.Target.Path) != "app.conf" {
			t.Errorf("target.path wrong: %+v", e.Target)
		}
		if e.Actor.Pid != 0 { // UNKNOWN sentinel
			t.Errorf("actor.pid = %d, want 0 (unknown)", e.Actor.Pid)
		}
		if err := e.Validate(); err != nil {
			t.Errorf("emitted invalid event: %v", err)
		}
	case <-ctx.Done():
		t.Fatal("no file.change event within timeout")
	}
}

// Reliability: a nonexistent watch dir is skipped, not fatal.
func TestNonexistentDirSkipped(t *testing.T) {
	cfg := config.Default()
	cfg.WatchPaths = []string{filepath.Join(t.TempDir(), "does-not-exist")}
	w, _ := New(cfg, quiet())
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	if err := w.Start(ctx, make(chan event.Event, 1)); err != nil {
		t.Errorf("Start should not error on a missing dir: %v", err)
	}
}

// With no watch paths, Start idles until ctx is done (so the fan-in still joins).
func TestNoWatchPathsIdles(t *testing.T) {
	w, _ := New(config.Default(), quiet())
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- w.Start(ctx, make(chan event.Event)) }()
	select {
	case err := <-done:
		if err != nil {
			t.Errorf("idle Start returned error: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("idle Start did not return after ctx cancel")
	}
}
