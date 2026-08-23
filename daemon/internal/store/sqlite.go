package store

import (
	"database/sql"
	"fmt"
	"os"

	"causegraph.dev/daemon/internal/event"

	_ "modernc.org/sqlite" // pure-Go, cgo-free driver; registers "sqlite"
)

// SchemaVersion is the store layout version, written to the meta table at
// creation so a future additive change is detectable by the engine.
const SchemaVersion = 1

// SQLite is the default Sink: an embedded, WAL-mode, row-capped ring buffer.
// It can never fill the disk it is meant to help diagnose (architecture.md §5.2).
type SQLite struct {
	db      *sql.DB
	maxRows int64
}

// OpenSQLite opens (creating if needed) the events DB at path, enables WAL, and
// ensures the schema + meta row exist. maxRows is the ring-buffer cap.
func OpenSQLite(path string, maxRows int64) (*SQLite, error) {
	// Create the DB file mode 0600 before opening so captured data — which includes full
	// command lines that can carry secrets — is never world-readable on a shared machine.
	if path != ":memory:" && path != "" {
		if f, err := os.OpenFile(path, os.O_CREATE, 0o600); err == nil {
			f.Close()
		}
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	// One writer at a time in SQLite; keep a single conn for writes but allow the
	// driver to serialize. Pragmas: WAL lets readers run concurrently with the
	// writer; busy_timeout avoids spurious "database is locked".
	for _, pragma := range []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA busy_timeout=5000",
		"PRAGMA synchronous=NORMAL",
	} {
		if _, err := db.Exec(pragma); err != nil {
			db.Close()
			return nil, fmt.Errorf("store: %s: %w", pragma, err)
		}
	}
	s := &SQLite{db: db, maxRows: maxRows}
	if err := s.init(); err != nil {
		db.Close()
		return nil, err
	}
	// The WAL/SHM sidecars hold the same data; lock them to 0600 too (best-effort — they
	// exist once WAL is initialised, and chmod on a missing file is harmless to ignore).
	if path != ":memory:" && path != "" {
		for _, suffix := range []string{"", "-wal", "-shm"} {
			_ = os.Chmod(path+suffix, 0o600)
		}
	}
	return s, nil
}

func (s *SQLite) init() error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS events (
			seq  INTEGER PRIMARY KEY AUTOINCREMENT,
			ts   INTEGER NOT NULL,
			pid  INTEGER NOT NULL,
			ppid INTEGER NOT NULL,
			kind TEXT    NOT NULL,
			data TEXT    NOT NULL
		)`,
		`CREATE INDEX IF NOT EXISTS idx_events_pid ON events(pid)`,
		`CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)`,
		`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`,
	}
	for _, st := range stmts {
		if _, err := s.db.Exec(st); err != nil {
			return fmt.Errorf("store: init: %w", err)
		}
	}
	if _, err := s.db.Exec(
		`INSERT INTO meta(key, value) VALUES('schema_version', ?)
		 ON CONFLICT(key) DO NOTHING`, fmt.Sprint(SchemaVersion)); err != nil {
		return fmt.Errorf("store: init meta: %w", err)
	}
	return nil
}

// WriteBatch inserts all events and evicts rows past maxRows in the SAME
// transaction, so an interrupt between batches always leaves row count <= maxRows.
func (s *SQLite) WriteBatch(events []event.Event) error {
	if len(events) == 0 {
		return nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback() //nolint:errcheck // no-op after a successful Commit

	ins, err := tx.Prepare(`INSERT INTO events(ts, pid, ppid, kind, data) VALUES(?,?,?,?,?)`)
	if err != nil {
		return err
	}
	defer ins.Close()

	for _, e := range events {
		data, err := event.Encode(e)
		if err != nil {
			return fmt.Errorf("store: encode: %w", err)
		}
		if _, err := ins.Exec(e.Ts, e.Actor.Pid, e.Actor.Ppid, e.Kind, string(data)); err != nil {
			return fmt.Errorf("store: insert: %w", err)
		}
	}

	// Evict oldest past the cap, atomic with the insert above. seq is monotonic,
	// so keeping seq > (max_seq - maxRows) keeps exactly the newest maxRows rows.
	if _, err := tx.Exec(
		`DELETE FROM events WHERE seq <= (SELECT COALESCE(MAX(seq),0) - ? FROM events)`,
		s.maxRows); err != nil {
		return fmt.Errorf("store: evict: %w", err)
	}
	return tx.Commit()
}

// Count returns the current number of stored events.
func (s *SQLite) Count() (int64, error) {
	var n int64
	err := s.db.QueryRow(`SELECT COUNT(*) FROM events`).Scan(&n)
	return n, err
}

// JournalMode returns the active journal mode (expect "wal").
func (s *SQLite) JournalMode() (string, error) {
	var mode string
	err := s.db.QueryRow(`PRAGMA journal_mode`).Scan(&mode)
	return mode, err
}

// MetaSchemaVersion reads the schema_version recorded at creation.
func (s *SQLite) MetaSchemaVersion() (string, error) {
	var v string
	err := s.db.QueryRow(`SELECT value FROM meta WHERE key='schema_version'`).Scan(&v)
	return v, err
}

// DB exposes the underlying handle for tests that need a second connection.
func (s *SQLite) DB() *sql.DB { return s.db }

func (s *SQLite) Close() error { return s.db.Close() }
