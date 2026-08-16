// Package event defines the canonical Event (generated in event_gen.go from
// shared/schema/event.schema.json) plus validation and codec. Nothing above the
// capture layer branches on OS; it branches on Event.Kind.
package event

import (
	"fmt"
	"unicode/utf8"
)

// MaxArgsBytes bounds the total size of an actor's args so a pathological
// command line cannot blow up memory or the store row. Oversized => rejected.
const MaxArgsBytes = 1 << 20 // 1 MiB

// Validate enforces the schema's closed enums and basic invariants that JSON
// shape alone cannot. Codec Encode/Decode both call it, so an invalid Event
// never reaches the store or crosses the language boundary.
func (e Event) Validate() error {
	if e.ID == "" {
		return fmt.Errorf("event: empty id")
	}
	if !ValidKinds[e.Kind] {
		return fmt.Errorf("event: invalid kind %q", e.Kind)
	}
	if !ValidSources[e.Source] {
		return fmt.Errorf("event: invalid source %q", e.Source)
	}
	if e.Confidence < 0 || e.Confidence > 1 {
		return fmt.Errorf("event: confidence %v out of [0,1]", e.Confidence)
	}
	if !utf8.ValidString(e.Actor.Exe) {
		return fmt.Errorf("event: actor.exe is not valid UTF-8")
	}
	if !utf8.ValidString(e.Actor.User) {
		return fmt.Errorf("event: actor.user is not valid UTF-8")
	}
	total := 0
	for _, a := range e.Actor.Args {
		if !utf8.ValidString(a) {
			return fmt.Errorf("event: actor.args contains non-UTF-8")
		}
		total += len(a)
	}
	if total > MaxArgsBytes {
		return fmt.Errorf("event: actor.args %d bytes exceeds cap %d", total, MaxArgsBytes)
	}
	return nil
}

// Helpers for building optional (pointer) fields concisely.
func StrPtr(s string) *string    { return &s }
func F64Ptr(f float64) *float64  { return &f }
func I64Ptr(i int64) *int64      { return &i }
