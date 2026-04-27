/**
 * @file machine.go
 * @description IVR 상태머신 — channel 기반 5-state FSM (교착 원천 차단)
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | IDLE→MENU→AI_CHAT/TRANSFER/DISCONNECT
 * v1.1.0 | 2026-04-09 | [Implementer] | T-20 | IVR inactivity timeout (default 5min)
 * ─────────────────────────────────────────
 */

package ivr

import (
	"context"
	"log/slog"
	"time"
)

// EventType identifies the IVR event kind.
type EventType int

const (
	DtmfEvent        EventType = iota
	HangupEvent
	ActivateMenuEvent
)

// IvrEvent is sent to the machine's event channel.
type IvrEvent struct {
	Type  EventType
	Digit string // for DtmfEvent: "0"-"9", "*", "#"
}

// Callbacks holds IVR transition action functions.
type Callbacks struct {
	OnEnterAiChat func()
	OnTransfer    func()
	OnDisconnect  func()
	OnRepeatMenu  func()
	OnForwardDtmf func(digit string)
}

// T-20: Default inactivity timeout (no DTMF activity in Menu state)
const defaultInactivityTimeout = 5 * time.Minute

// Scenario defines the entire IVR flow.
type Scenario struct {
	ID           string              `json:"id"`
	InitialState string              `json:"initial_state"`
	Nodes        map[string]IvrNode `json:"nodes"`
}

// IvrNode defines a single state's behavior and transitions.
type IvrNode struct {
	Prompt     string            `json:"prompt,omitempty"`
	OnDtmf     map[string]string `json:"on_dtmf,omitempty"` // digit -> next_state
	OnTimeout  string            `json:"on_timeout,omitempty"`
	TimeoutMs  int               `json:"timeout_ms,omitempty"`
	Action     string            `json:"action,omitempty"` // e.g., "START_AI", "TRANSFER", "DISCONNECT"
	Target     string            `json:"target,omitempty"` // for transfer
}

// Machine is a channel-based IVR FSM.
type Machine struct {
	sessionID string
	state     string
	scenario  *Scenario
	EventCh   chan IvrEvent
	cb        Callbacks
}

// NewMachine creates a new IVR machine with a dynamic scenario.
func NewMachine(sessionID string, scenario *Scenario, cb Callbacks) *Machine {
	if scenario == nil {
		// Fallback to minimal default scenario
		scenario = &Scenario{
			InitialState: "IDLE",
			Nodes: map[string]IvrNode{
				"IDLE": {OnDtmf: map[string]string{}},
			},
		}
	}
	return &Machine{
		sessionID: sessionID,
		state:     scenario.InitialState,
		scenario:  scenario,
		EventCh:   make(chan IvrEvent, 16),
		cb:        cb,
	}
}

// Run processes events until context is cancelled. Must be called as a goroutine.
func (m *Machine) Run(ctx context.Context) {
	slog.Info("IVR machine started", "session", m.sessionID, "state", m.state)
	inactivityTimer := time.NewTimer(defaultInactivityTimeout)
	defer inactivityTimer.Stop()

	for {
		select {
		case <-ctx.Done():
			slog.Info("IVR machine stopped", "session", m.sessionID)
			return
		case evt := <-m.EventCh:
			m.handleEvent(evt)
			inactivityTimer.Reset(defaultInactivityTimeout)
		case <-inactivityTimer.C:
			slog.Warn("IVR inactivity timeout", "session", m.sessionID, "state", m.state)
			if m.cb.OnDisconnect != nil {
				m.cb.OnDisconnect()
			}
			return
		}
	}
}

// State returns the current FSM state (as string for dynamic scenario).
func (m *Machine) State() string {
	return m.state
}

func (m *Machine) handleEvent(evt IvrEvent) {
	switch evt.Type {
	case ActivateMenuEvent:
		// Transition to initial menu state
		m.transition(m.scenario.InitialState)
		if m.cb.OnRepeatMenu != nil {
			m.cb.OnRepeatMenu()
		}

	case HangupEvent:
		m.transition("IDLE")

	case DtmfEvent:
		m.handleDtmf(evt.Digit)
	}
}

func (m *Machine) handleDtmf(digit string) {
	slog.Info("IVR DTMF received", "session", m.sessionID, "digit", digit, "state", m.state)

	node, ok := m.scenario.Nodes[m.state]
	if !ok {
		slog.Error("IVR: current state node not found", "session", m.sessionID, "state", m.state)
		return
	}

	// 1. Check for state transition
	nextState, found := node.OnDtmf[digit]
	if !found {
		// Fallback: forward to AI if in AI_CHAT node or as default behavior
		if node.Action == "START_AI" {
			slog.Info("IVR: forwarding DTMF to AI", "session", m.sessionID, "digit", digit)
			if m.cb.OnForwardDtmf != nil {
				m.cb.OnForwardDtmf(digit)
			}
		} else {
			slog.Warn("IVR: unhandled DTMF digit", "session", m.sessionID, "digit", digit, "state", m.state)
		}
		return
	}

	// 2. Perform transition
	m.transition(nextState)

	// 3. Execute actions for the NEW state
	newNode := m.scenario.Nodes[nextState]
	m.executeAction(newNode)
}

func (m *Machine) transition(newState string) {
	slog.Info("IVR state transition",
		"session", m.sessionID,
		"from", m.state,
		"to", newState,
	)
	m.state = newState
}

func (m *Machine) executeAction(node IvrNode) {
	switch node.Action {
	case "START_AI":
		if m.cb.OnEnterAiChat != nil {
			m.cb.OnEnterAiChat()
		}
	case "TRANSFER":
		if m.cb.OnTransfer != nil {
			// In a real scenario, we'd pass node.Target here
			m.cb.OnTransfer()
		}
	case "DISCONNECT":
		if m.cb.OnDisconnect != nil {
			m.cb.OnDisconnect()
		}
	case "REPEAT_MENU":
		if m.cb.OnRepeatMenu != nil {
			m.cb.OnRepeatMenu()
		}
	}
}
