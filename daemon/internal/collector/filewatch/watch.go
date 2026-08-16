// Package filewatch is a cross-platform file.change collector built on fsnotify
// (kqueue/inotify/ReadDirectoryChangesW) — no root, no entitlement. It emits
// canonical file.change events with target.path set and an UNKNOWN actor sentinel
// (fsnotify gives no process attribution; a native backend would fill the writer
// pid). It is a second Collector alongside the generic process poller.
package filewatch

import (
	"context"
	"io/fs"
	"log"
	"path/filepath"
	"time"

	"causegraph.dev/daemon/internal/collector"
	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"

	"github.com/fsnotify/fsnotify"
	"github.com/google/uuid"
)

// opsThatMatter are the fsnotify operations we treat as a file change. Pure Chmod
// is dropped as noise (metadata churn is not causally interesting here).
const opsThatMatter = fsnotify.Create | fsnotify.Write | fsnotify.Remove | fsnotify.Rename

type Watcher struct {
	cfg    config.Config
	hostID string
	logger *log.Logger
}

func New(cfg config.Config, logger *log.Logger) (*Watcher, error) {
	if logger == nil {
		logger = log.Default()
	}
	return &Watcher{cfg: cfg, hostID: cfg.HostID, logger: logger}, nil
}

func (w *Watcher) Capabilities() collector.Caps {
	return collector.Caps{
		ProcessSpawn:  false,
		ProcessExit:   false,
		FileEvents:    true,
		SocketEvents:  false,
		ResourceStats: false,
		Fidelity:      collector.FidelityNative, // real-time kernel notifications
	}
}

func (w *Watcher) Close() error { return nil }

// Start watches cfg.WatchPaths (recursively adding subdirectories present at
// startup) and emits a file.change event per interesting fs event until ctx ends.
// A path that cannot be added is logged and skipped — never fatal.
func (w *Watcher) Start(ctx context.Context, out chan<- event.Event) error {
	if len(w.cfg.WatchPaths) == 0 {
		<-ctx.Done() // nothing to watch; idle until shutdown so the fan-in still joins
		return nil
	}
	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		return err
	}
	defer watcher.Close()

	for _, root := range w.cfg.WatchPaths {
		w.addRecursive(watcher, root)
	}

	for {
		select {
		case <-ctx.Done():
			return nil
		case ev, ok := <-watcher.Events:
			if !ok {
				return nil
			}
			if ev.Op&opsThatMatter == 0 {
				continue // pure Chmod / uninteresting
			}
			if !send(ctx, out, w.fileEvent(ev.Name)) {
				return nil
			}
		case err, ok := <-watcher.Errors:
			if !ok {
				return nil
			}
			w.logger.Printf("filewatch: %v", err)
		}
	}
}

// addRecursive adds root and every subdirectory present now. New subdirectories
// created later are not auto-watched (documented limitation).
func (w *Watcher) addRecursive(watcher *fsnotify.Watcher, root string) {
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil // skip unreadable entries, keep walking
		}
		if d.IsDir() {
			if aerr := watcher.Add(path); aerr != nil {
				w.logger.Printf("filewatch: cannot watch %s: %v", path, aerr)
			}
		}
		return nil
	})
	if err != nil {
		w.logger.Printf("filewatch: cannot walk %s (skipped): %v", root, err)
	}
}

func (w *Watcher) fileEvent(path string) event.Event {
	return event.Event{
		ID:     uuid.NewString(),
		Ts:     time.Now().UnixNano(),
		HostID: w.hostID,
		Kind:   event.KindFileChange,
		// UNKNOWN actor: fsnotify has no process attribution. Sentinel pid 0.
		Actor:      event.Actor{Pid: 0, Ppid: 0, Exe: "", Args: []string{}, User: ""},
		Target:     &event.Target{Path: event.StrPtr(path)},
		Source:     event.SourceFsnotify,
		Confidence: 1.0, // the file change is an observed fact; the *causal link* is scored later
	}
}

func send(ctx context.Context, out chan<- event.Event, e event.Event) bool {
	select {
	case out <- e:
		return true
	case <-ctx.Done():
		return false
	}
}
