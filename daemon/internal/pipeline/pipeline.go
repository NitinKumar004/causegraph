package pipeline

import (
	"context"
	"log"
	"sync/atomic"
	"time"

	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
	"causegraph.dev/daemon/internal/store"
)

// Pipeline drains a drop-oldest Buffer into a Sink in batches. Two distinct drop
// counters are kept on purpose: overflow (store slow / backpressure) and
// write-error (store broken) demand opposite responses, so one counter would
// erase the signal a self-observability tool needs.
type Pipeline struct {
	cfg  config.Config
	sink store.Sink
	buf  *Buffer

	droppedWriteError atomic.Int64
	logger            *log.Logger
}

func New(cfg config.Config, sink store.Sink, logger *log.Logger) *Pipeline {
	if logger == nil {
		logger = log.Default()
	}
	return &Pipeline{
		cfg:    cfg,
		sink:   sink,
		buf:    NewBuffer(cfg.BufferSize),
		logger: logger,
	}
}

// Ingest hands an event to the buffer. Never blocks the caller (the collector),
// even if the store is stalled — the buffer drops oldest instead.
func (p *Pipeline) Ingest(e event.Event) { p.buf.Push(e) }

// Run drains the buffer to the sink on a timer until ctx is cancelled, then does
// a final flush so a clean shutdown (SIGINT) loses nothing already buffered.
func (p *Pipeline) Run(ctx context.Context) error {
	ticker := time.NewTicker(p.cfg.BatchInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			p.drainAll()
			return nil
		case <-ticker.C:
			p.drainAll()
		}
	}
}

// drainAll flushes the whole buffer in BatchSize chunks. A sink error does not
// retry: it is logged, the batch's events counted into dropped_write_error, and
// draining continues so a broken store cannot wedge the pipeline.
func (p *Pipeline) drainAll() {
	for {
		batch := p.buf.PopBatch(p.cfg.BatchSize)
		if len(batch) == 0 {
			return
		}
		if err := p.sink.WriteBatch(batch); err != nil {
			p.droppedWriteError.Add(int64(len(batch)))
			p.logger.Printf("pipeline: store write error, dropped %d events: %v", len(batch), err)
		}
	}
}

func (p *Pipeline) DroppedOverflow() int64   { return p.buf.DroppedOverflow() }
func (p *Pipeline) DroppedWriteError() int64 { return p.droppedWriteError.Load() }
func (p *Pipeline) Buffered() int            { return p.buf.Len() }
