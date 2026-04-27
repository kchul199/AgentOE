package grpc

import (
	"context"
	"testing"
	"time"
)

func TestNewPool(t *testing.T) {
	pool := NewPool("127.0.0.1:50051", false)
	if pool == nil {
		t.Fatal("expected non-nil pool")
	}
	if pool.addr != "127.0.0.1:50051" {
		t.Fatalf("expected addr 127.0.0.1:50051, got %s", pool.addr)
	}
}

func TestPool_GetStream_NoClient(t *testing.T) {
	pool := NewPool("127.0.0.1:50051", false)
	// Don't call Connect — client is nil

	ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
	defer cancel()

	_, err := pool.GetStream(ctx, "test-uuid")
	if err == nil {
		t.Fatal("expected error when client is nil")
	}
}

func TestPool_RemoveStream_NonExistent(t *testing.T) {
	pool := NewPool("127.0.0.1:50051", false)
	// Should not panic
	pool.RemoveStream("non-existent-uuid")
}

func TestPool_Close_Empty(t *testing.T) {
	pool := NewPool("127.0.0.1:50051", false)
	// Should not panic
	pool.Close()
}

func TestStream_Send_ContextCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Already cancelled

	// Unbuffered channel — forces select to choose ctx.Done() branch
	s := &Stream{
		uuid:   "test",
		sendCh: make(chan sendMsg), // unbuffered
		ctx:    ctx,
		cancel: cancel,
	}

	err := s.Send("session-1", []byte("audio"), true)
	if err == nil {
		t.Fatal("expected error on cancelled context")
	}
}

func TestStream_SendDtmf_Success(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	s := &Stream{
		uuid:   "test",
		sendCh: make(chan sendMsg, 10),
		ctx:    ctx,
		cancel: cancel,
	}

	err := s.SendDtmf("session-1", "5")
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}

	// Verify message was queued
	msg := <-s.sendCh
	if msg.dtmfDigit != "5" {
		t.Fatalf("expected digit '5', got '%s'", msg.dtmfDigit)
	}
}

func TestStream_Recv_ChannelClosed(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	recvCh := make(chan *AiResponse, 10)
	close(recvCh)

	s := &Stream{
		uuid:   "test",
		recvCh: recvCh,
		ctx:    ctx,
		cancel: cancel,
	}

	_, err := s.Recv()
	if err == nil {
		t.Fatal("expected error when recv channel is closed")
	}
}

func TestStream_Recv_ContextCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())

	s := &Stream{
		uuid:   "test",
		recvCh: make(chan *AiResponse), // unbuffered, will block
		ctx:    ctx,
		cancel: cancel,
	}

	cancel() // Cancel before recv

	_, err := s.Recv()
	if err == nil {
		t.Fatal("expected error on cancelled context")
	}
}

func TestStream_Send_Success(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	s := &Stream{
		uuid:   "test",
		sendCh: make(chan sendMsg, 10),
		ctx:    ctx,
		cancel: cancel,
	}

	err := s.Send("session-1", []byte("audio-data"), true)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}

	msg := <-s.sendCh
	if msg.sessionID != "session-1" {
		t.Fatalf("expected session-1, got %s", msg.sessionID)
	}
	if !msg.isSpeaking {
		t.Fatal("expected isSpeaking=true")
	}
}

func TestStream_Recv_Success(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	recvCh := make(chan *AiResponse, 10)
	recvCh <- &AiResponse{Type: 1, TextContent: "hello", ClearBuffer: false}

	s := &Stream{
		uuid:   "test",
		recvCh: recvCh,
		ctx:    ctx,
		cancel: cancel,
	}

	resp, err := s.Recv()
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if resp.TextContent != "hello" {
		t.Fatalf("expected 'hello', got '%s'", resp.TextContent)
	}
}
