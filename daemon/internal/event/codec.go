package event

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// Encode marshals an Event to compact JSON. HTML escaping is disabled so the
// bytes match what other languages (Python) produce for the same value, and the
// Event is validated first so no invalid Event is ever emitted.
func Encode(e Event) ([]byte, error) {
	if err := e.Validate(); err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(e); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// Decode parses JSON into an Event. Unknown fields are rejected (the schema is
// closed: additionalProperties=false), and the result is validated.
func Decode(b []byte) (Event, error) {
	var e Event
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&e); err != nil {
		return Event{}, fmt.Errorf("event: decode: %w", err)
	}
	if err := e.Validate(); err != nil {
		return Event{}, err
	}
	return e, nil
}
