package generic

import (
	"context"
	"fmt"
	"testing"
	"time"

	"causegraph.dev/daemon/internal/collector"
	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"
)

// generic.Poller must satisfy the capture seam (AC9). This assertion lives in the
// test to avoid an import cycle while still enforcing the contract at build time.
var _ collector.Collector = (*Poller)(nil)

func seqID() func() string {
	n := 0
	return func() string { n++; return fmt.Sprintf("id%d", n) }
}

func pi(pid, ppid int64, create int64) procInfo {
	return procInfo{pid: pid, ppid: ppid, exe: "/bin/x", args: []string{}, user: "u", createNs: create}
}

// AC5: snapshot diff produces spawn for new pids and exit for gone pids.
func TestDiffSpawnAndExit(t *testing.T) {
	prev := snapshot{1: pi(1, 0, 100)}
	cur := snapshot{1: pi(1, 0, 100), 2: pi(2, 1, 200)} // 2 is new
	evs := diff(prev, cur, "h", 500, seqID())
	if len(evs) != 1 || evs[0].Kind != event.KindProcessSpawn || evs[0].Actor.Pid != 2 {
		t.Fatalf("expected one spawn for pid 2, got %+v", evs)
	}

	// Now pid 1 disappears.
	evs = diff(cur, snapshot{2: pi(2, 1, 200)}, "h", 600, seqID())
	if len(evs) != 1 || evs[0].Kind != event.KindProcessExit || evs[0].Actor.Pid != 1 {
		t.Fatalf("expected one exit for pid 1, got %+v", evs)
	}
}

// AC5 / AC7: pid reuse (same pid, different create time) => exit(old)+spawn(new),
// never a single collapsed process.
func TestDiffPidReuse(t *testing.T) {
	prev := snapshot{100: pi(100, 1, 111)}
	cur := snapshot{100: pi(100, 9, 222)} // same pid, new create time + new parent
	evs := diff(prev, cur, "h", 700, seqID())
	if len(evs) != 2 {
		t.Fatalf("pid reuse should yield 2 events, got %d: %+v", len(evs), evs)
	}
	if evs[0].Kind != event.KindProcessExit || evs[0].Actor.Ppid != 1 {
		t.Errorf("first should be exit of old (ppid 1): %+v", evs[0])
	}
	if evs[1].Kind != event.KindProcessSpawn || evs[1].Actor.Ppid != 9 {
		t.Errorf("second should be spawn of new (ppid 9): %+v", evs[1])
	}
}

func TestDiffNoChange(t *testing.T) {
	s := snapshot{1: pi(1, 0, 100), 2: pi(2, 1, 200)}
	if evs := diff(s, s, "h", 800, seqID()); len(evs) != 0 {
		t.Fatalf("no change should yield 0 events, got %+v", evs)
	}
}

// AC5: resource.sample only for "processes that matter" (cpu OR rss over threshold).
func TestSampleThreshold(t *testing.T) {
	cfg := config.Default()
	cfg.SampleMinCPUPct = 5.0
	cfg.SampleMinRSS = 100 << 20 // 100 MiB
	p := &Poller{cfg: cfg, hostID: "h"}

	hot := procInfo{pid: 1, ppid: 0, exe: "/x", args: []string{}, user: "u", cpuPct: 50, rssBytes: 1024}
	fat := procInfo{pid: 2, ppid: 0, exe: "/x", args: []string{}, user: "u", cpuPct: 0, rssBytes: 200 << 20}
	idle := procInfo{pid: 3, ppid: 0, exe: "/x", args: []string{}, user: "u", cpuPct: 0.1, rssBytes: 1024}
	snap := snapshot{1: hot, 2: fat, 3: idle}

	evs := p.sampleEventsWithID(snap, 900, seqID())
	if len(evs) != 2 {
		t.Fatalf("expected 2 samples (hot+fat), got %d", len(evs))
	}
	for _, e := range evs {
		if e.Kind != event.KindResourceSample || e.Metrics == nil {
			t.Errorf("bad sample: %+v", e)
		}
		if e.Actor.Pid == 3 {
			t.Errorf("idle process should not be sampled")
		}
	}
}

// M1a: the adaptive cadence — churn snaps to min, sustained quiet backs off to max.
func TestNextIntervalAdapts(t *testing.T) {
	min, max := 250*time.Millisecond, 2*time.Second

	// churn always snaps straight to the fast cadence and resets the quiet counter.
	if got, q := nextInterval(max, min, max, true, 2); got != min || q != 0 {
		t.Fatalf("churn: got (%v,%d), want (%v,0)", got, q, min)
	}

	// from min, quiet ticks accumulate, then back off by doubling on the 3rd.
	iv, q := min, 0
	iv, q = nextInterval(iv, min, max, false, q) // quiet 1: hold
	if iv != min || q != 1 {
		t.Fatalf("quiet#1: got (%v,%d), want (%v,1)", iv, q, min)
	}
	iv, q = nextInterval(iv, min, max, false, q) // quiet 2: hold
	if iv != min || q != 2 {
		t.Fatalf("quiet#2: got (%v,%d), want (%v,2)", iv, q, min)
	}
	iv, q = nextInterval(iv, min, max, false, q) // quiet 3: back off, reset
	if iv != 2*min || q != 0 {
		t.Fatalf("quiet#3: got (%v,%d), want (%v,0)", iv, q, 2*min)
	}

	// back-off is clamped at max.
	if got, _ := nextInterval(2*time.Second, min, max, false, backoffQuiet-1); got != max {
		t.Fatalf("clamp: got %v, want %v", got, max)
	}
}

// M1a: PollMax<=0 falls back to SampleInterval; PollMin>=max disables adaptation.
func TestPollBounds(t *testing.T) {
	p := &Poller{cfg: config.Config{SampleInterval: 2 * time.Second, PollMin: 250 * time.Millisecond}}
	if min, max := p.pollBounds(); min != 250*time.Millisecond || max != 2*time.Second {
		t.Fatalf("adaptive bounds: got (%v,%v)", min, max)
	}
	p2 := &Poller{cfg: config.Config{SampleInterval: 2 * time.Second, PollMin: 3 * time.Second}}
	if min, max := p2.pollBounds(); min != max { // min >= max disables adaptation
		t.Fatalf("disabled adaptation should give min==max, got (%v,%v)", min, max)
	}
}

func TestCapabilities(t *testing.T) {
	p, _ := New(config.Default())
	c := p.Capabilities()
	if !c.ProcessSpawn || !c.ProcessExit || !c.ResourceStats {
		t.Errorf("generic caps missing process/resource: %+v", c)
	}
	if c.FileEvents || c.SocketEvents {
		t.Errorf("polling cannot observe file/socket events: %+v", c)
	}
	if c.Fidelity != collector.FidelityPolling {
		t.Errorf("fidelity = %q, want polling", c.Fidelity)
	}
}

// Smoke test: Start against the real process list emits valid events and honours
// context cancellation (this is the only test that touches gopsutil).
func TestStartSmoke(t *testing.T) {
	cfg := config.Default()
	cfg.SampleInterval = 20 * time.Millisecond
	cfg.SampleMinCPUPct = 0    // sample everything so we definitely get events
	cfg.SampleMinRSS = 0
	p, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	out := make(chan event.Event, 4096)
	done := make(chan error, 1)
	go func() { done <- p.Start(ctx, out) }()

	<-ctx.Done()
	if err := <-done; err != nil {
		t.Fatalf("Start returned error: %v", err)
	}
	close(out)
	n := 0
	for e := range out {
		if err := e.Validate(); err != nil {
			t.Fatalf("emitted invalid event: %v (%+v)", err, e)
		}
		n++
	}
	if n == 0 {
		t.Error("expected at least one event from the live process list")
	}
}
