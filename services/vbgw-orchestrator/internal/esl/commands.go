/**
 * @file commands.go
 * @description ESL uuid_* 명령 래퍼 — FreeSWITCH 콜 제어 API
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | 12개 ESL 명령 래퍼
 * v1.1.0 | 2026-04-09 | [Implementer] | FS-2,FS-3 | Eavesdrop, Conference, AttendedTransfer 추가
 * ─────────────────────────────────────────
 */

package esl

import (
	"context"
	"fmt"
	"strings"
)

// Originate starts an outbound call via the PBX gateway.
// P-07: callerID parameter for CID display (Korean telecom law).
// P-08: Returns bgapi Job-UUID; use CHANNEL_HANGUP event for SIP response code.
// P-11/Q-03: Tries pbx-main first, falls back to pbx-standby if configured.
func (c *Client) Originate(ctx context.Context, uuid, target, callerID string, useStandby bool) (string, error) {
	cidParam := ""
	if callerID != "" {
		cidParam = fmt.Sprintf(",origination_caller_id_number=%s,origination_caller_id_name=%s",
			callerID, callerID)
	}

	gateway := fmt.Sprintf("sofia/gateway/pbx-main/%s", target)
	// Q-03: Only add standby failover pipe if standby PBX is configured
	if useStandby {
		gateway += fmt.Sprintf("|sofia/gateway/pbx-standby/%s", target)
	}

	cmd := fmt.Sprintf(
		"originate {origination_uuid=%s%s,failure_causes=NORMAL_TEMPORARY_FAILURE,originate_timeout=30}%s &park()",
		uuid, cidParam, gateway,
	)
	return c.SendBgAPI(ctx, cmd)
}

// SendDtmf sends DTMF digits to a channel.
func (c *Client) SendDtmf(ctx context.Context, uuid, digits string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_send_dtmf %s %s", uuid, digits))
	return err
}

// Transfer performs a blind transfer.
func (c *Client) Transfer(ctx context.Context, uuid, target string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_transfer %s %s XML default", uuid, target))
	return err
}

// Bridge bridges two channels (1:1).
func (c *Client) Bridge(ctx context.Context, uuidA, uuidB string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_bridge %s %s", uuidA, uuidB))
	return err
}

// Unbridge parks a channel to detach from bridge.
func (c *Client) Unbridge(ctx context.Context, uuid string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_transfer %s -both park", uuid))
	return err
}

// RecordStart begins recording a channel.
func (c *Client) RecordStart(ctx context.Context, uuid, path string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_record %s start %s", uuid, path))
	return err
}

// RecordStop stops recording a channel.
func (c *Client) RecordStop(ctx context.Context, uuid string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_record %s stop", uuid))
	return err
}

// Break interrupts the current playback on a channel (barge-in).
func (c *Client) Break(ctx context.Context, uuid string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_break %s all", uuid))
	return err
}

// Kill terminates a channel.
func (c *Client) Kill(ctx context.Context, uuid string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_kill %s", uuid))
	return err
}

// Dump returns all channel variables as key=value pairs.
func (c *Client) Dump(ctx context.Context, uuid string) (map[string]string, error) {
	resp, err := c.SendAPI(ctx, fmt.Sprintf("uuid_dump %s", uuid))
	if err != nil {
		return nil, err
	}

	// Check for FS API error responses
	if strings.Contains(resp, "-ERR") || strings.Contains(resp, "-USAGE") {
		return nil, fmt.Errorf("uuid_dump failed: %s", strings.TrimSpace(resp))
	}

	result := make(map[string]string)
	for _, line := range strings.Split(resp, "\n") {
		line = strings.TrimSpace(line)
		if idx := strings.Index(line, "="); idx > 0 {
			result[line[:idx]] = line[idx+1:]
		}
	}
	return result, nil
}

// Pause sends fsctl pause to stop accepting new calls.
func (c *Client) Pause(ctx context.Context) error {
	_, err := c.SendAPI(ctx, "fsctl pause")
	return err
}

// Resume sends fsctl resume to accept new calls again.
func (c *Client) Resume(ctx context.Context) error {
	_, err := c.SendAPI(ctx, "fsctl resume")
	return err
}

// Hupall hangs up all active calls.
func (c *Client) Hupall(ctx context.Context) error {
	_, err := c.SendAPI(ctx, "hupall")
	return err
}

// Eavesdrop allows a supervisor to listen to a call.
func (c *Client) Eavesdrop(ctx context.Context, supervisorUUID, targetUUID string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_transfer %s 'eavesdrop::%s' inline", supervisorUUID, targetUUID))
	return err
}

// ConferenceKick removes a participant from a conference.
func (c *Client) ConferenceKick(ctx context.Context, confName, memberID string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("conference %s kick %s", confName, memberID))
	return err
}

// AttendedTransfer performs a two-step attended (consultative) transfer.
func (c *Client) AttendedTransfer(ctx context.Context, uuid, target string) error {
	_, err := c.SendAPI(ctx, fmt.Sprintf("uuid_transfer %s 'att_xfer::{origination_caller_id_name=Transfer}sofia/gateway/pbx-main/%s' inline", uuid, target))
	return err
}
