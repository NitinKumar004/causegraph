package event

import (
	"bufio"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// fixturePath resolves the shared cross-language fixture relative to the repo root.
func fixturePath(t *testing.T) string {
	t.Helper()
	// event package lives at daemon/internal/event; repo root is three up.
	p := filepath.Join("..", "..", "..", "test", "fixtures", "events.jsonl")
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("fixture not found: %v", err)
	}
	return p
}

func readFixture(t *testing.T) []Event {
	t.Helper()
	f, err := os.Open(fixturePath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var out []Event
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<20)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		e, err := Decode([]byte(line))
		if err != nil {
			t.Fatalf("decode fixture line: %v\n%s", err, line)
		}
		out = append(out, e)
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

// AC2: codec round-trips every canonical Event losslessly (Go encode->decode).
func TestRoundTripFixture(t *testing.T) {
	events := readFixture(t)
	if len(events) != 4 {
		t.Fatalf("expected 4 fixture events, got %d", len(events))
	}
	for i, e := range events {
		b, err := Encode(e)
		if err != nil {
			t.Fatalf("event %d encode: %v", i, err)
		}
		got, err := Decode(b)
		if err != nil {
			t.Fatalf("event %d re-decode: %v", i, err)
		}
		if !reflect.DeepEqual(e, got) {
			t.Errorf("event %d not lossless:\n want %+v\n got  %+v", i, e, got)
		}
	}
}

// AC2 cross-language contract: the same fixture must decode to the SAME values in
// Go and Python. Both test suites assert these exact values, so agreeing with the
// expectation means agreeing with each other.
func TestFixtureExactValues(t *testing.T) {
	events := readFixture(t)

	hb := events[0]
	if hb.Kind != KindHeartbeat || hb.Actor.Pid != 42 || hb.Actor.Ppid != 1 ||
		hb.Actor.Exe != "/usr/bin/cged" || len(hb.Actor.Args) != 0 ||
		hb.Source != SourcePoll || hb.Confidence != 1.0 || hb.Target != nil || hb.Metrics != nil {
		t.Errorf("heartbeat mismatch: %+v", hb)
	}

	spawn := events[1]
	if spawn.Kind != KindProcessSpawn || spawn.Actor.Pid != 100 || spawn.Actor.Ppid != 42 ||
		!reflect.DeepEqual(spawn.Actor.Args, []string{"bash", "-c", "sleep 1"}) ||
		spawn.Actor.User != "alice" {
		t.Errorf("spawn mismatch: %+v", spawn)
	}

	rs := events[3]
	if rs.Kind != KindResourceSample || rs.Metrics == nil {
		t.Fatalf("resource.sample missing metrics: %+v", rs)
	}
	if rs.Metrics.CPUPct == nil || *rs.Metrics.CPUPct != 12.5 {
		t.Errorf("cpu_pct want 12.5, got %v", rs.Metrics.CPUPct)
	}
	if rs.Metrics.RSSBytes == nil || *rs.Metrics.RSSBytes != 1048576 {
		t.Errorf("rss_bytes want 1048576, got %v", rs.Metrics.RSSBytes)
	}
	if rs.Metrics.TempC != nil {
		t.Errorf("temp_c should be absent, got %v", *rs.Metrics.TempC)
	}
}

// AC (security): codec rejects invalid input, never panics.
func TestValidationRejects(t *testing.T) {
	base := Event{
		ID: "x", Ts: 1, HostID: "h", Kind: KindHeartbeat,
		Actor:  Actor{Pid: 1, Ppid: 0, Exe: "/x", Args: []string{}, User: "u"},
		Source: SourcePoll, Confidence: 1.0,
	}
	cases := map[string]func(e *Event){
		"bad kind":       func(e *Event) { e.Kind = "process.forkbomb" },
		"bad source":     func(e *Event) { e.Source = "kprobe" },
		"empty id":       func(e *Event) { e.ID = "" },
		"conf>1":         func(e *Event) { e.Confidence = 1.5 },
		"conf<0":         func(e *Event) { e.Confidence = -0.1 },
		"non-utf8 exe":   func(e *Event) { e.Actor.Exe = string([]byte{0xff, 0xfe}) },
		"oversized args": func(e *Event) { e.Actor.Args = []string{strings.Repeat("a", MaxArgsBytes+1)} },
	}
	for name, mut := range cases {
		e := base
		e.Actor.Args = append([]string{}, base.Actor.Args...)
		mut(&e)
		if err := e.Validate(); err == nil {
			t.Errorf("%s: expected validation error, got nil", name)
		}
		if _, err := Encode(e); err == nil {
			t.Errorf("%s: expected Encode to reject", name)
		}
	}
}

// file.change round-trips losslessly, including target.path and the UNKNOWN actor.
func TestFileChangeRoundTrip(t *testing.T) {
	e := Event{
		ID: "f1", Ts: 5, HostID: "h", Kind: KindFileChange,
		Actor:  Actor{Pid: 0, Ppid: 0, Exe: "", Args: []string{}, User: ""},
		Target: &Target{Path: StrPtr("/etc/app.conf")},
		Source: SourceFsnotify, Confidence: 1.0,
	}
	b, err := Encode(e)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	got, err := Decode(b)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !reflect.DeepEqual(e, got) {
		t.Errorf("file.change not lossless:\n want %+v\n got %+v", e, got)
	}
}

// AC2 security: target.path is validated (non-UTF8 / oversized rejected).
func TestTargetPathValidation(t *testing.T) {
	base := Event{
		ID: "f", Ts: 1, HostID: "h", Kind: KindFileChange,
		Actor:  Actor{Pid: 0, Ppid: 0, Exe: "", Args: []string{}, User: ""},
		Source: SourceFsnotify, Confidence: 1.0,
	}
	bad := base
	bad.Target = &Target{Path: StrPtr(string([]byte{0xff, 0xfe}))}
	if err := bad.Validate(); err == nil {
		t.Error("expected non-UTF8 target.path to be rejected")
	}
	big := base
	big.Target = &Target{Path: StrPtr(strings.Repeat("a", MaxPathBytes+1))}
	if err := big.Validate(); err == nil {
		t.Error("expected oversized target.path to be rejected")
	}
}

// AC (security): unknown fields are rejected (closed schema).
func TestDecodeRejectsUnknownField(t *testing.T) {
	bad := `{"id":"x","ts":1,"host_id":"h","kind":"heartbeat","actor":{"pid":1,"ppid":0,"exe":"/x","args":[],"user":"u"},"source":"poll","confidence":1.0,"rogue":true}`
	if _, err := Decode([]byte(bad)); err == nil {
		t.Fatal("expected decode to reject unknown field 'rogue'")
	}
}
