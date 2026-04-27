package esl

import (
	"testing"
)

func TestParseEvent_BasicHeaders(t *testing.T) {
	data := "Event-Name: CHANNEL_CREATE\nUnique-ID: abc-123\nCaller-Caller-ID-Number: 01012345678\nCaller-Destination-Number: 100\n"

	evt := ParseEvent(data)

	if evt.Name() != "CHANNEL_CREATE" {
		t.Fatalf("expected CHANNEL_CREATE, got %s", evt.Name())
	}
	if evt.UUID() != "abc-123" {
		t.Fatalf("expected abc-123, got %s", evt.UUID())
	}
	if evt.CallerID() != "01012345678" {
		t.Fatalf("expected 01012345678, got %s", evt.CallerID())
	}
	if evt.DestNumber() != "100" {
		t.Fatalf("expected 100, got %s", evt.DestNumber())
	}
}

func TestParseEvent_URLEncoded(t *testing.T) {
	data := "Event-Name: DTMF\nDTMF-Digit: %23\n"

	evt := ParseEvent(data)

	if evt.DtmfDigit() != "#" {
		t.Fatalf("expected '#' (URL-decoded), got %s", evt.DtmfDigit())
	}
}

func TestParseEvent_WithBody(t *testing.T) {
	data := "Content-Type: text/event-plain\nContent-Length: 11\n\nHello World"

	evt := ParseEvent(data)

	if evt.Body != "Hello World" {
		t.Fatalf("expected 'Hello World', got '%s'", evt.Body)
	}
}

func TestParseEvent_EmptyInput(t *testing.T) {
	evt := ParseEvent("")

	if evt.Name() != "" {
		t.Fatalf("expected empty name, got %s", evt.Name())
	}
	if len(evt.Headers) != 0 {
		t.Fatalf("expected 0 headers, got %d", len(evt.Headers))
	}
}

func TestParseEvent_CustomSubClass(t *testing.T) {
	data := "Event-Name: CUSTOM\nEvent-Subclass: sofia%3A%3Aregister\n"

	evt := ParseEvent(data)

	if evt.Name() != "CUSTOM" {
		t.Fatalf("expected CUSTOM, got %s", evt.Name())
	}
	if evt.SubClass() != "sofia::register" {
		t.Fatalf("expected sofia::register, got %s", evt.SubClass())
	}
}

func TestParseEvent_HangupCause(t *testing.T) {
	data := "Event-Name: CHANNEL_HANGUP_COMPLETE\nHangup-Cause: NORMAL_CLEARING\nvariable_sip_term_status: 200\n"

	evt := ParseEvent(data)

	if evt.HangupCause() != "NORMAL_CLEARING" {
		t.Fatalf("expected NORMAL_CLEARING, got %s", evt.HangupCause())
	}
	if evt.SipTermStatus() != "200" {
		t.Fatalf("expected 200, got %s", evt.SipTermStatus())
	}
}

func TestParseEvent_MissingHeader(t *testing.T) {
	data := "Event-Name: CHANNEL_CREATE\n"

	evt := ParseEvent(data)

	// Missing headers should return empty string, not panic
	if evt.UUID() != "" {
		t.Fatalf("expected empty UUID, got %s", evt.UUID())
	}
	if evt.DtmfDigit() != "" {
		t.Fatalf("expected empty DtmfDigit, got %s", evt.DtmfDigit())
	}
}

func TestEvent_Get_URLDecodeFailure(t *testing.T) {
	evt := &Event{
		Headers: map[string]string{
			"Bad-Header": "%ZZ",
		},
	}

	// Should return raw value when URL decode fails
	if evt.Get("Bad-Header") != "%ZZ" {
		t.Fatalf("expected raw %%ZZ, got %s", evt.Get("Bad-Header"))
	}
}
