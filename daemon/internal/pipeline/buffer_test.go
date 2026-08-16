package pipeline

import (
	"fmt"
	"sync"
	"testing"

	"causegraph.dev/daemon/internal/event"
)

func ev(ts int64) event.Event {
	return event.Event{
		ID: fmt.Sprintf("e%d", ts), Ts: ts, HostID: "h", Kind: event.KindProcessSpawn,
		Actor:  event.Actor{Pid: ts, Ppid: 1, Exe: "/x", Args: []string{}, User: "u"},
		Source: event.SourcePoll, Confidence: 1.0,
	}
}

// AC4 (blocking finding): fill buffer to cap, push +1 => oldest evicted, newest
// retained, dropped_overflow == 1.
func TestPushEvictsOldest(t *testing.T) {
	b := NewBuffer(3)
	for i := int64(0); i < 3; i++ {
		b.Push(ev(i)) // ts 0,1,2 fill the buffer
	}
	if b.DroppedOverflow() != 0 {
		t.Fatalf("no drop expected yet, got %d", b.DroppedOverflow())
	}
	b.Push(ev(3)) // overflow: evicts oldest (ts 0)
	if b.DroppedOverflow() != 1 {
		t.Fatalf("dropped_overflow=%d, want 1", b.DroppedOverflow())
	}
	got := b.PopBatch(10)
	var tss []int64
	for _, e := range got {
		tss = append(tss, e.Ts)
	}
	want := []int64{1, 2, 3} // oldest (0) gone, newest (3) retained
	if fmt.Sprint(tss) != fmt.Sprint(want) {
		t.Errorf("retained ts = %v, want %v", tss, want)
	}
}

func TestPopBatchFIFOAndSize(t *testing.T) {
	b := NewBuffer(100)
	for i := int64(0); i < 10; i++ {
		b.Push(ev(i))
	}
	first := b.PopBatch(4)
	if len(first) != 4 || first[0].Ts != 0 || first[3].Ts != 3 {
		t.Fatalf("first batch wrong: %+v", first)
	}
	if b.Len() != 6 {
		t.Fatalf("len=%d, want 6", b.Len())
	}
}

// AC concurrency: N producers + 1 drainer against the shared buffer under -race.
// Conservation: pushed == popped + dropped + remaining.
func TestConcurrentProducersDrainer(t *testing.T) {
	const producers = 8
	const perProducer = 2000
	b := NewBuffer(1024)

	var wg sync.WaitGroup
	stop := make(chan struct{})
	var popped int64
	var drainWg sync.WaitGroup
	drainWg.Add(1)
	go func() {
		defer drainWg.Done()
		for {
			select {
			case <-stop:
				popped += int64(len(drainAllOnce(b)))
				return
			default:
				popped += int64(len(b.PopBatch(64)))
			}
		}
	}()

	for p := 0; p < producers; p++ {
		wg.Add(1)
		go func(p int) {
			defer wg.Done()
			for i := 0; i < perProducer; i++ {
				b.Push(ev(int64(p*perProducer + i)))
			}
		}(p)
	}
	wg.Wait()
	close(stop)
	drainWg.Wait()

	total := int64(producers * perProducer)
	if popped+b.DroppedOverflow()+int64(b.Len()) != total {
		t.Errorf("conservation broken: popped=%d dropped=%d remaining=%d total=%d",
			popped, b.DroppedOverflow(), b.Len(), total)
	}
}

func drainAllOnce(b *Buffer) []event.Event {
	var all []event.Event
	for {
		batch := b.PopBatch(256)
		if len(batch) == 0 {
			return all
		}
		all = append(all, batch...)
	}
}
