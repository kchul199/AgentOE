package api

import (
	"testing"
)

func TestDtmfPattern_Valid(t *testing.T) {
	valid := []string{
		"1", "0", "#", "*",
		"1234567890",
		"*#", "ABCD",
		"123*#0",
	}
	for _, v := range valid {
		if !dtmfPattern.MatchString(v) {
			t.Errorf("expected '%s' to be valid DTMF", v)
		}
	}
}

func TestDtmfPattern_Invalid(t *testing.T) {
	invalid := []string{
		"",
		"E",
		"1\n2",
		"123456789012345678901",
		"1 2",
		"abc",
		"1;drop table",
	}
	for _, v := range invalid {
		if dtmfPattern.MatchString(v) {
			t.Errorf("expected '%s' to be INVALID DTMF", v)
		}
	}
}

func TestSipTargetPattern_Valid(t *testing.T) {
	valid := []string{
		"1000",
		"sip:agent@pbx",
		"1001@192.168.1.1:5060",
		"sofia/gateway/pbx-main/1000",
		"agent_queue",
		"agent-1.dept",
	}
	for _, v := range valid {
		if !sipTargetPattern.MatchString(v) {
			t.Errorf("expected '%s' to be valid SIP target", v)
		}
	}
}

func TestSipTargetPattern_Invalid(t *testing.T) {
	invalid := []string{
		"",
		"target with spaces",
		"target\nnewline",
		"target;evil",
		"target&evil",
		"target$(cmd)",
	}
	for _, v := range invalid {
		if sipTargetPattern.MatchString(v) {
			t.Errorf("expected '%s' to be INVALID SIP target", v)
		}
	}
}

func TestMaskURI(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"sip:1234@pbx", "********@pbx"},
		{"01012345678", "*******5678"},
		{"ab", "****"},
		{"", "****"},
		{"abcd", "****"},
		{"abcde", "*bcde"},
	}
	for _, tc := range tests {
		got := maskURI(tc.input)
		if got != tc.expected {
			t.Errorf("maskURI(%q) = %q, want %q", tc.input, got, tc.expected)
		}
	}
}
