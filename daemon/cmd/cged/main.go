// Command cged is the CauseGraph capture daemon. It wires config -> collector ->
// pipeline -> store and writes canonical events to SQLite. The collector is used
// ONLY through the collector.Collector interface, so a native backend (M3) is one
// new file with nothing here changing except the constructor selection.
package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"os/user"
	"strings"
	"sync"
	"syscall"
	"time"

	"causegraph.dev/daemon/internal/collector"
	"causegraph.dev/daemon/internal/collector/filewatch"
	"causegraph.dev/daemon/internal/collector/generic"
	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
	"causegraph.dev/daemon/internal/pipeline"
	"causegraph.dev/daemon/internal/store"

	"github.com/google/uuid"
)

func main() {
	cfg := config.Default()
	var duration time.Duration
	flag.StringVar(&cfg.DBPath, "db", cfg.DBPath, "SQLite database path")
	flag.DurationVar(&cfg.SampleInterval, "interval", cfg.SampleInterval, "poll/sample interval")
	flag.Int64Var(&cfg.MaxRows, "max-rows", cfg.MaxRows, "events table ring-buffer cap")
	flag.DurationVar(&cfg.HeartbeatInterval, "heartbeat", cfg.HeartbeatInterval, "heartbeat interval")
	flag.DurationVar(&duration, "duration", 0, "run for this long then exit (0 = until SIGINT)")
	var watch string
	flag.StringVar(&watch, "watch", "", "comma-separated directories to watch for file changes")
	flag.Parse()
	if watch != "" {
		for _, p := range strings.Split(watch, ",") {
			if p = strings.TrimSpace(p); p != "" {
				cfg.WatchPaths = append(cfg.WatchPaths, p)
			}
		}
	}

	logger := log.New(os.Stderr, "cged ", log.LstdFlags)

	rootCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if duration > 0 {
		var cancel context.CancelFunc
		rootCtx, cancel = context.WithTimeout(rootCtx, duration)
		defer cancel()
	}

	if err := run(rootCtx, cfg, logger); err != nil {
		logger.Fatalf("cged: %v", err)
	}
}

// run wires and drives the daemon until ctx is cancelled, then shuts down cleanly
// (collector stops, every buffered event is flushed) before returning. Extracted
// from main so the full lifecycle is testable (AC6).
func run(ctx context.Context, cfg config.Config, logger *log.Logger) error {
	if cfg.HostID == "" {
		if h, err := os.Hostname(); err == nil {
			cfg.HostID = h
		}
	}

	sink, err := store.OpenSQLite(cfg.DBPath, cfg.MaxRows)
	if err != nil {
		return err
	}
	defer sink.Close()

	// Backend selection: the ONLY place concrete backends are named. Everything
	// below sees collector.Collector. Multiple backends feed one channel.
	var cols []collector.Collector
	pol, err := generic.New(cfg)
	if err != nil {
		return err
	}
	cols = append(cols, pol)
	fw, err := filewatch.New(cfg, logger)
	if err != nil {
		return err
	}
	cols = append(cols, fw)
	for _, c := range cols {
		defer c.Close() // part of the Collector seam; native backends release OS handles here
		logger.Printf("capture: %T caps=%+v", c, c.Capabilities())
	}

	p := pipeline.New(cfg, sink, logger)

	// Pipeline drain loop under its own context so we can flush AFTER the bridge
	// has moved every last collected event into the buffer.
	pctx, pcancel := context.WithCancel(context.Background())
	pipeDone := make(chan struct{})
	go func() { _ = p.Run(pctx); close(pipeDone) }()

	out := make(chan event.Event, cfg.BufferSize)
	bridgeDone := make(chan struct{})
	go func() {
		for e := range out {
			p.Ingest(e)
		}
		close(bridgeDone)
	}()

	// heartbeat: emitted by main (owns liveness), directly into the buffer. We
	// wait for it to stop BEFORE the final flush, otherwise a tick racing ctx
	// cancellation could Ingest after the last drainAll and be lost.
	heartbeatDone := make(chan struct{})
	go func() { heartbeatLoop(ctx, cfg, p); close(heartbeatDone) }()

	// collectors: each runs in its own goroutine into the shared out channel; out
	// is closed EXACTLY once, after all collectors return (WaitGroup fan-in), so
	// no goroutine can double-close it.
	var cwg sync.WaitGroup
	for _, c := range cols {
		cwg.Add(1)
		go func(c collector.Collector) {
			defer cwg.Done()
			if err := c.Start(ctx, out); err != nil {
				logger.Printf("collector %T stopped: %v", c, err)
			}
		}(c)
	}
	go func() { cwg.Wait(); close(out) }()

	logger.Printf("cged running, writing to %s (Ctrl-C to stop)", cfg.DBPath)
	<-ctx.Done()
	<-bridgeDone    // collector returned and every collected event is buffered
	<-heartbeatDone // no more heartbeat Ingests can happen after this
	pcancel()       // trigger the single final flush — nothing writes to the buffer now
	<-pipeDone

	logger.Printf("shutdown: dropped_overflow=%d dropped_write_error=%d",
		p.DroppedOverflow(), p.DroppedWriteError())
	return nil
}

func heartbeatLoop(ctx context.Context, cfg config.Config, p *pipeline.Pipeline) {
	emit := func() { p.Ingest(heartbeatEvent(cfg)) }
	emit() // one immediately at startup
	t := time.NewTicker(cfg.HeartbeatInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			emit()
		}
	}
}

func heartbeatEvent(cfg config.Config) event.Event {
	exe, _ := os.Executable()
	uname := ""
	if u, err := user.Current(); err == nil {
		uname = u.Username
	}
	return event.Event{
		ID:     uuid.NewString(),
		Ts:     time.Now().UnixNano(),
		HostID: cfg.HostID,
		Kind:   event.KindHeartbeat,
		Actor: event.Actor{
			Pid:  int64(os.Getpid()),
			Ppid: int64(os.Getppid()),
			Exe:  exe,
			Args: []string{},
			User: uname,
		},
		Source:     event.SourcePoll,
		Confidence: 1.0,
	}
}
