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
	"syscall"
	"time"

	"causegraph.dev/daemon/internal/collector"
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
	flag.Parse()

	logger := log.New(os.Stderr, "cged ", log.LstdFlags)
	if cfg.HostID == "" {
		if h, err := os.Hostname(); err == nil {
			cfg.HostID = h
		}
	}

	sink, err := store.OpenSQLite(cfg.DBPath, cfg.MaxRows)
	if err != nil {
		logger.Fatalf("open store: %v", err)
	}
	defer sink.Close()

	// Backend selection: the ONLY place a concrete backend is named. Everything
	// below sees collector.Collector.
	var col collector.Collector
	pol, err := generic.New(cfg)
	if err != nil {
		logger.Fatalf("collector: %v", err)
	}
	col = pol
	logger.Printf("capture: generic poll backend, caps=%+v", col.Capabilities())

	p := pipeline.New(cfg, sink, logger)

	rootCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if duration > 0 {
		var cancel context.CancelFunc
		rootCtx, cancel = context.WithTimeout(rootCtx, duration)
		defer cancel()
	}

	// pipeline drain loop under its own context so we can flush AFTER the bridge
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

	// heartbeat: emitted by main (owns liveness), directly into the buffer.
	go heartbeatLoop(rootCtx, cfg, p)

	// collector: on return, close out so the bridge finishes.
	go func() {
		if err := col.Start(rootCtx, out); err != nil {
			logger.Printf("collector stopped: %v", err)
		}
		close(out)
	}()

	logger.Printf("cged running, writing to %s (Ctrl-C to stop)", cfg.DBPath)
	<-rootCtx.Done()
	<-bridgeDone // collector returned and every collected event is buffered
	pcancel()    // trigger final flush
	<-pipeDone

	logger.Printf("shutdown: dropped_overflow=%d dropped_write_error=%d",
		p.DroppedOverflow(), p.DroppedWriteError())
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
