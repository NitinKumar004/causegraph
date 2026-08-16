// Package generic is the cross-platform capture floor: it polls the process list
// via gopsutil and diffs snapshots to infer spawns/exits, plus coarse resource
// sampling. Lower fidelity than native backends (it can miss processes shorter-
// lived than one poll interval) but it compiles and runs on macOS/Windows/Linux
// with zero root/entitlements (architecture.md §3.2).
package generic

import (
	"context"
	"os"
	"time"

	"causegraph.dev/daemon/internal/collector"
	"causegraph.dev/daemon/internal/config"
	"causegraph.dev/daemon/internal/event"

	"github.com/google/uuid"
	"github.com/shirou/gopsutil/v4/process"
)

// procInfo is one process at one instant. createNs (from gopsutil CreateTime,
// which is ms since epoch) disambiguates pid reuse: same pid + different create
// time is a different process.
type procInfo struct {
	pid, ppid int64
	exe       string
	args      []string
	user      string
	createNs  int64
	cpuPct    float64
	rssBytes  int64
}

type snapshot map[int64]procInfo

// Poller implements collector.Collector using gopsutil.
type Poller struct {
	cfg    config.Config
	hostID string
}

func New(cfg config.Config) (*Poller, error) {
	host := cfg.HostID
	if host == "" {
		if h, err := os.Hostname(); err == nil {
			host = h
		}
	}
	return &Poller{cfg: cfg, hostID: host}, nil
}

func (p *Poller) Capabilities() collector.Caps {
	return collector.Caps{
		ProcessSpawn:  true,
		ProcessExit:   true,
		FileEvents:    false, // polling can't see file events; native backend (M3) adds them
		SocketEvents:  false,
		ResourceStats: true,
		Fidelity:      collector.FidelityPolling,
	}
}

func (p *Poller) Close() error { return nil }

// Start runs one poll tick every SampleInterval. A tick both diffs the current
// snapshot against the previous one (spawn/exit) and emits resource samples for
// processes that matter — one config value governs both cadences. The very first
// snapshot is a baseline (no spawn flood for already-running processes).
func (p *Poller) Start(ctx context.Context, out chan<- event.Event) error {
	prev := p.scan()
	// Emit an initial resource sample for the baseline so the store is not empty
	// until the first process starts/stops.
	for _, e := range p.sampleEvents(prev, nowNs()) {
		if !send(ctx, out, e) {
			return nil
		}
	}

	ticker := time.NewTicker(p.cfg.SampleInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			cur := p.scan()
			ts := nowNs()
			evs := diff(prev, cur, p.hostID, ts, uuid.NewString)
			evs = append(evs, p.sampleEventsWithID(cur, ts, uuid.NewString)...)
			for _, e := range evs {
				if !send(ctx, out, e) {
					return nil
				}
			}
			prev = cur
		}
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

func nowNs() int64 { return time.Now().UnixNano() }

// scan builds a snapshot from the live process list. Processes that error out
// mid-scan (they exited) are skipped rather than failing the whole tick.
func (p *Poller) scan() snapshot {
	snap := make(snapshot)
	procs, err := process.Processes()
	if err != nil {
		return snap
	}
	for _, pr := range procs {
		pid := int64(pr.Pid)
		ppid32, err := pr.Ppid()
		if err != nil {
			continue
		}
		createMs, err := pr.CreateTime()
		if err != nil {
			continue
		}
		exe, _ := pr.Exe()
		args, _ := pr.CmdlineSlice()
		user, _ := pr.Username()
		// CPUPercent's first read per process is coarse/0 (gopsutil needs two
		// reads to compute a delta); acceptable for sampling semantics.
		cpu, _ := pr.CPUPercent()
		var rss int64
		if mi, err := pr.MemoryInfo(); err == nil && mi != nil {
			rss = int64(mi.RSS)
		}
		if args == nil {
			args = []string{}
		}
		snap[pid] = procInfo{
			pid:      pid,
			ppid:     int64(ppid32),
			exe:      exe,
			args:     args,
			user:     user,
			createNs: createMs * 1_000_000, // ms -> ns
			cpuPct:   cpu,
			rssBytes: rss,
		}
	}
	return snap
}

// sampleEvents emits resource.sample events for the baseline using uuid ids.
func (p *Poller) sampleEvents(snap snapshot, ts int64) []event.Event {
	return p.sampleEventsWithID(snap, ts, uuid.NewString)
}

func (p *Poller) sampleEventsWithID(snap snapshot, ts int64, mkID func() string) []event.Event {
	var out []event.Event
	for _, pi := range snap {
		if pi.cpuPct >= p.cfg.SampleMinCPUPct || pi.rssBytes >= p.cfg.SampleMinRSS {
			out = append(out, resourceSample(pi, p.hostID, ts, mkID()))
		}
	}
	return out
}
