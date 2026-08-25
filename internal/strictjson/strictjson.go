// Package strictjson decodes one closed JSON object without duplicate keys.
package strictjson

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
)

// Decode checks duplicate keys, trailing JSON, unknown fields, and, when
// requested, canonical stored bytes before it decodes into destination.
func Decode(content []byte, destination any, requireCanonical bool) error {
	var generic any
	first := json.NewDecoder(bytes.NewReader(content))
	first.UseNumber()
	if err := scanValue(first); err != nil {
		return err
	}
	if _, err := first.Token(); err != io.EOF {
		if err == nil {
			return fmt.Errorf("JSON contains trailing data")
		}
		return fmt.Errorf("decode trailing JSON: %w", err)
	}

	genericDecoder := json.NewDecoder(bytes.NewReader(content))
	genericDecoder.UseNumber()
	if err := genericDecoder.Decode(&generic); err != nil {
		return fmt.Errorf("decode JSON: %w", err)
	}
	if requireCanonical {
		var canonical bytes.Buffer
		encoder := json.NewEncoder(&canonical)
		encoder.SetEscapeHTML(false)
		if err := encoder.Encode(generic); err != nil {
			return fmt.Errorf("encode canonical JSON: %w", err)
		}
		if !bytes.Equal(content, canonical.Bytes()) {
			return fmt.Errorf("stored JSON bytes are not canonical")
		}
	}

	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return fmt.Errorf("decode closed JSON: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return fmt.Errorf("JSON contains trailing data")
	}
	return nil
}

func scanValue(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return fmt.Errorf("decode JSON token: %w", err)
	}
	delimiter, compound := token.(json.Delim)
	if !compound {
		return nil
	}
	switch delimiter {
	case '{':
		seen := map[string]bool{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return fmt.Errorf("decode JSON object key: %w", err)
			}
			key, ok := keyToken.(string)
			if !ok {
				return fmt.Errorf("JSON object key is not a string")
			}
			if seen[key] {
				return fmt.Errorf("JSON object contains duplicate key %q", key)
			}
			seen[key] = true
			if err := scanValue(decoder); err != nil {
				return err
			}
		}
	case '[':
		for decoder.More() {
			if err := scanValue(decoder); err != nil {
				return err
			}
		}
	default:
		return fmt.Errorf("unexpected JSON delimiter %q", delimiter)
	}
	closing, err := decoder.Token()
	if err != nil {
		return fmt.Errorf("decode JSON closing delimiter: %w", err)
	}
	want := json.Delim('}')
	if delimiter == '[' {
		want = json.Delim(']')
	}
	if closing != want {
		return fmt.Errorf("JSON closing delimiter does not match")
	}
	return nil
}
