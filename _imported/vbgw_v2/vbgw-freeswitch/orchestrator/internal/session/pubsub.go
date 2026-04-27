/**
 * @file pubsub.go
 * @description Redis Pub/Sub을 사용한 노드 간 콜 제어 명령 라우팅
 */

package session

import (
	"context"
	"encoding/json"
	"log/slog"
)

// CommandMsg represents a control command to be routed to a specific node.
type CommandMsg struct {
	SessionID string          `json:"session_id"`
	Action    string          `json:"action"` // "transfer", "dtmf", etc.
	Payload   json.RawMessage `json:"payload"`
}

// PublishCommand routes a control command via Redis Pub/Sub to the specific node.
func (rs *RedisStore) PublishCommand(ctx context.Context, targetNodeID, sessionID, action string, payload interface{}) error {
	data, _ := json.Marshal(payload)
	msg := CommandMsg{
		SessionID: sessionID,
		Action:    action,
		Payload:   data,
	}
	body, _ := json.Marshal(msg)
	
	channel := "vbgw:node:" + targetNodeID + ":cmds"
	return rs.client.Publish(ctx, channel, body).Err()
}

// SubscribeCommands listens for commands targeted at this specific node.
// This should be run in a goroutine on startup.
func (rs *RedisStore) SubscribeCommands(ctx context.Context, handler func(msg CommandMsg)) {
	channel := "vbgw:node:" + rs.nodeID + ":cmds"
	pubsub := rs.client.Subscribe(ctx, channel)
	defer pubsub.Close()
	
	ch := pubsub.Channel()
	slog.Info("Subscribed to node command channel", "channel", channel)

	for {
		select {
		case <-ctx.Done():
			return
		case msg := <-ch:
			var cmd CommandMsg
			if err := json.Unmarshal([]byte(msg.Payload), &cmd); err == nil {
				// Execute handler in a goroutine to not block the pubsub listener
				go handler(cmd)
			} else {
				slog.Error("Failed to parse pubsub command", "err", err)
			}
		}
	}
}
