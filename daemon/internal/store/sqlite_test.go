package store

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"testing"

	"causegraph.dev/daemon/internal/event"

	_ "modernc.org/sqlite"
)

func mkEvent(i int) event.Event {
	return event.Event{
		ID: fmt.Sprintf("e%d", i), Ts: int64(i), HostID: "h",
		Kind:  event.KindProcessSpawn,
		Actor: event.Actor{Pid: int64(i), Ppid: 1, Exe: "/x", Args: []string{}, User: "u"},
		Source: event.SourcePoll, Confidence: 1.0,
	}
}

func openTmp(t *testing.T, maxRows int64) *SQLite {
	t.Helper()
	p := filepath.Join(t.TempDir(), "test.db")
	s, err := OpenSQLite(p, maxRows)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

// AC3: WAL enabled.
func TestWALEnabled(t *testing.T) {
	s := openTmp(t, 100)
	mode, err := s.JournalMode()
	if err != nil {
		t.Fatal(err)
	}
	if mode != "wal" {
		t.Errorf("journal_mode = %q, want wal", mode)
	}
}

// AC3: meta schema_version written at creation and readable.
func TestMetaSchemaVersion(t *testing.T) {
	s := openTmp(t, 100)
	v, err := s.MetaSchemaVersion()
	if err != nil {
		t.Fatal(err)
	}
	if v != fmt.Sprint(SchemaVersion) {
		t.Errorf("schema_version = %q, want %d", v, SchemaVersion)
	}
}

// AC3: row count capped at MaxRows; oldest deleted past cap; batched insert.
func TestRingBufferEvictsOldest(t *testing.T) {
	const cap = 10
	s := openTmp(t, cap)
	// Write 25 events across 5 batches of 5.
	for b := 0; b < 5; b++ {
		var batch []event.Event
		for i := 0; i < 5; i++ {
			batch = append(batch, mkEvent(b*5+i))
		}
		if err := s.WriteBatch(batch); err != nil {
			t.Fatalf("batch %d: %v", b, err)
		}
		// Invariant after every batch: never exceeds cap (atomic evict-in-txn).
		n, _ := s.Count()
		if n > cap {
			t.Fatalf("after batch %d count=%d exceeds cap=%d", b, n, cap)
		}
	}
	n, _ := s.Count()
	if n != cap {
		t.Fatalf("final count=%d, want %d", n, cap)
	}
	// Oldest (ts 0..14) evicted; newest (ts 15..24) retained.
	var minTs, maxTs int64
	if err := s.DB().QueryRow(`SELECT MIN(ts), MAX(ts) FROM events`).Scan(&minTs, &maxTs); err != nil {
		t.Fatal(err)
	}
	if minTs != 15 || maxTs != 24 {
		t.Errorf("retained ts range [%d,%d], want [15,24]", minTs, maxTs)
	}
}

// AC3 reliability: rows <= MaxRows survives reopen (each batch commits atomically
// with its evict, so any interrupt between batches leaves the invariant intact).
// NOTE: a true kill -9 mid-COMMIT is out of unit-test scope; the atomic
// insert+evict transaction is what guarantees it and is exercised above.
func TestCapInvariantAcrossReopen(t *testing.T) {
	p := filepath.Join(t.TempDir(), "reopen.db")
	s, err := OpenSQLite(p, 8)
	if err != nil {
		t.Fatal(err)
	}
	for b := 0; b < 3; b++ {
		var batch []event.Event
		for i := 0; i < 5; i++ {
			batch = append(batch, mkEvent(b*5+i))
		}
		if err := s.WriteBatch(batch); err != nil {
			t.Fatal(err)
		}
	}
	s.Close()

	s2, err := OpenSQLite(p, 8)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	n, _ := s2.Count()
	if n > 8 {
		t.Errorf("after reopen count=%d exceeds cap 8", n)
	}
	if v, _ := s2.MetaSchemaVersion(); v != fmt.Sprint(SchemaVersion) {
		t.Errorf("meta schema_version after reopen = %q, want %d", v, SchemaVersion)
	}
}

// AC concurrency: a reader on a second connection can SELECT the live DB while a
// writer holds an open transaction — WAL + busy_timeout => no "database is locked".
func TestWALConcurrentRead(t *testing.T) {
	p := filepath.Join(t.TempDir(), "wal.db")
	s, err := OpenSQLite(p, 1000)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.WriteBatch([]event.Event{mkEvent(1), mkEvent(2)}); err != nil {
		t.Fatal(err)
	}

	// Writer holds an open write transaction.
	tx, err := s.DB().Begin()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Exec(`INSERT INTO events(ts,pid,ppid,kind,data) VALUES(9,9,1,'heartbeat','{}')`); err != nil {
		t.Fatal(err)
	}

	// Independent reader connection.
	rdb, err := sql.Open("sqlite", p+"?_pragma=busy_timeout(5000)")
	if err != nil {
		t.Fatal(err)
	}
	defer rdb.Close()
	var n int
	if err := rdb.QueryRow(`SELECT COUNT(*) FROM events`).Scan(&n); err != nil {
		t.Fatalf("concurrent read failed (lock?): %v", err)
	}
	if n != 2 {
		t.Errorf("reader saw %d rows, want 2 (last committed snapshot)", n)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
}
