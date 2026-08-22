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

// backoffQuiet: consecutive empty diff ticks before the cadence backs off a step.
const backoffQuiet = 3

// nextInterval picks the next process-diff cadence. Churn snaps to min (poll fast to
// catch short-lived processes); sustained quiet (>= backoffQuiet empty ticks) doubles
// toward max so an idle machine costs nothing. Pure, so it is unit-tested.
func nextInterval(cur, min, max time.Duration, churn bool, quiet int) (time.Duration, int) {
	if churn {
		return min, 0
	}
	if quiet+1 >= backoffQuiet {
		next := cur * 2
		if next > max {
			next = max
		}
		return next, 0
	}
	return cur, quiet + 1
}

// pollBounds resolves the adaptive diff cadence window. PollMax<=0 means "use
// SampleInterval"; a PollMin that is unset or >= max disables adaptation (fixed at max).
func (p *Poller) pollBounds() (min, max time.Duration) {
	max = p.cfg.PollMax
	if max <= 0 {
		max = p.cfg.SampleInterval
	}
	min = p.cfg.PollMin
	if min <= 0 || min >= max {
		min = max
	}
	return min, max
}

// Start runs two independent cadences (architecture.md §3.2, M1a):
//   - an ADAPTIVE process-diff timer in [PollMin, PollMax] — fast during churn to catch
//     short-lived processes, backing off when idle. Its scan skips CPU/RSS (cheap).
//   - a fixed SampleInterval resource-sample ticker (CPU/RSS need no sub-second cadence).
//
// The first snapshot is a baseline (no spawn flood for already-running processes).
func (p *Poller) Start(ctx context.Context, out chan<- event.Event) error {
	prev := p.scan(true)
	// Emit an initial resource sample for the baseline so the store is not empty
	// until the first process starts/stops.
	for _, e := range p.sampleEvents(prev, nowNs()) {
		if !send(ctx, out, e) {
			return nil
		}
	}

	min, max := p.pollBounds()
	interval, quiet := max, 0 // start idle
	diffTimer := time.NewTimer(interval)
	defer diffTimer.Stop()
	resTicker := time.NewTicker(p.cfg.SampleInterval)
	defer resTicker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-diffTimer.C:
			cur := p.scan(false) // identity only — no CPU/RSS on the fast path
			ts := nowNs()
			evs := diff(prev, cur, p.hostID, ts, uuid.NewString)
			for _, e := range evs {
				if !send(ctx, out, e) {
					return nil
				}
			}
			prev = cur
			interval, quiet = nextInterval(interval, min, max, len(evs) > 0, quiet)
			diffTimer.Reset(interval)
		case <-resTicker.C:
			snap := p.scan(true)
			ts := nowNs()
			for _, e := range p.sampleEventsWithID(snap, ts, uuid.NewString) {
				if !send(ctx, out, e) {
					return nil
				}
			}
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
// mid-scan (they exited) are skipped rather than failing the whole tick. When
// withMetrics is false (the fast diff path) it skips CPUPercent()+MemoryInfo() —
// the two costly per-process calls — since spawn/exit detection needs only identity
// (pid/ppid/createNs/exe/args/user). createNs is always fetched so pid-reuse stays
// detectable on the fast path.
func (p *Poller) scan(withMetrics bool) snapshot {
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
		var cpu float64
		var rss int64
		if withMetrics {
			// CPUPercent's first read per process is coarse/0 (gopsutil needs two
			// reads to compute a delta); acceptable for sampling semantics.
			cpu, _ = pr.CPUPercent()
			if mi, err := pr.MemoryInfo(); err == nil && mi != nil {
				rss = int64(mi.RSS)
			}
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
