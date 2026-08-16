package generic

import (
	"sort"

	"causegraph.dev/daemon/internal/event"
)

// diff turns two consecutive snapshots into spawn/exit events. It is pure and
// deterministic (pids processed in sorted order) so it is unit-tested without
// gopsutil. Pid reuse — same pid, different create time — yields an exit for the
// old process and a spawn for the new one, so the two never collapse into one
// graph node downstream.
func diff(prev, cur snapshot, hostID string, ts int64, mkID func() string) []event.Event {
	var out []event.Event

	curPids := sortedPids(cur)
	for _, pid := range curPids {
		c := cur[pid]
		pInfo, existed := prev[pid]
		switch {
		case !existed:
			out = append(out, spawnEvent(c, hostID, ts, mkID()))
		case pInfo.createNs != c.createNs:
			// pid reuse: old process gone, new one born on the same pid.
			out = append(out, exitEvent(pInfo, hostID, ts, mkID()))
			out = append(out, spawnEvent(c, hostID, ts, mkID()))
		}
	}

	for _, pid := range sortedPids(prev) {
		if _, stillThere := cur[pid]; !stillThere {
			out = append(out, exitEvent(prev[pid], hostID, ts, mkID()))
		}
	}
	return out
}

func sortedPids(s snapshot) []int64 {
	pids := make([]int64, 0, len(s))
	for pid := range s {
		pids = append(pids, pid)
	}
	sort.Slice(pids, func(i, j int) bool { return pids[i] < pids[j] })
	return pids
}

func actorOf(pi procInfo) event.Actor {
	args := pi.args
	if args == nil {
		args = []string{}
	}
	return event.Actor{
		Pid: pi.pid, Ppid: pi.ppid, Exe: pi.exe, Args: args, User: pi.user,
	}
}

func baseEvent(kind, hostID string, ts int64, id string, actor event.Actor) event.Event {
	return event.Event{
		ID: id, Ts: ts, HostID: hostID, Kind: kind,
		Actor: actor, Source: event.SourcePoll, Confidence: 1.0,
	}
}

func spawnEvent(pi procInfo, hostID string, ts int64, id string) event.Event {
	return baseEvent(event.KindProcessSpawn, hostID, ts, id, actorOf(pi))
}

func exitEvent(pi procInfo, hostID string, ts int64, id string) event.Event {
	return baseEvent(event.KindProcessExit, hostID, ts, id, actorOf(pi))
}

func resourceSample(pi procInfo, hostID string, ts int64, id string) event.Event {
	e := baseEvent(event.KindResourceSample, hostID, ts, id, actorOf(pi))
	e.Metrics = &event.Metrics{
		CPUPct:   event.F64Ptr(pi.cpuPct),
		RSSBytes: event.I64Ptr(pi.rssBytes),
	}
	return e
}
