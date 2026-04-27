package ai

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"vbgw-ai/internal/config"

	pb "vbgw-ai/proto/voicebot"
)

type Server struct {
	pb.UnimplementedVoicebotAiServiceServer
	engine SpeechEngine
}

func NewServer(engine SpeechEngine) *Server {
	return &Server{
		engine: engine,
	}
}

func (s *Server) StreamSession(stream pb.VoicebotAiService_StreamSessionServer) error {
	var sessionID string
	var lastSpeaking bool = false
	var audioBuffer bytes.Buffer
	var greetingSent bool = false

	for {
		chunk, err := stream.Recv()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			slog.Error("Stream recv error", "err", err)
			return err
		}

		if sessionID == "" && chunk.SessionId != "" {
			sessionID = chunk.SessionId
			slog.Info("New AI session started", "session_id", sessionID)
		}

		if !greetingSent && sessionID != "" {
			greetingSent = true
			slog.Info("Triggering initial greeting", "session_id", sessionID)
			go s.sendInitialGreeting(stream, sessionID)
		}

		// 1. 발화 중(IsSpeaking=true)이면 오디오를 버퍼에 수집
		if chunk.IsSpeaking {
			audioBuffer.Write(chunk.AudioData)
		}

		// 2. 발화 종료(Fall-edge) 감지 — AI 파이프라인 가동
		if lastSpeaking && !chunk.IsSpeaking {
			slog.Info("End of speech detected — processing pipeline", "session_id", sessionID)
			
			// 버퍼에서 데이터 추출 및 초기화
			capturedAudio := audioBuffer.Bytes()
			audioBuffer.Reset()

			// 쿠션 멘트 즉시 송출 (사용자 대기감 해소)
			s.sendCushionPhrase(stream)

			// 비동기로 AI 로직 처리 (지연 시간 최소화를 위해 고루틴 활용)
			go s.processAIResponse(stream, sessionID, capturedAudio)
		}

		lastSpeaking = chunk.IsSpeaking
	}
}

// sendInitialGreeting: 세션 시작 시 최초 인사말 송출
func (s *Server) sendInitialGreeting(stream pb.VoicebotAiService_StreamSessionServer, sessionID string) {
	ctx := context.Background()
	greetingText := config.AppConfig.OpenAI.GreetingMsg
	if greetingText == "" {
		greetingText = "안녕하세요, 보이스봇입니다. 무엇을 도와드릴까요?"
	}

	slog.Info("Sending initial greeting", "session_id", sessionID, "text", greetingText)

	// TTS 합성
	raw24kAudio, err := s.engine.Synthesize(ctx, greetingText)
	if err != nil {
		slog.Error("Initial greeting TTS failed", "session_id", sessionID, "err", err)
		return
	}

	audioResponse := Resample24To16(raw24kAudio)
	chunkSize := 640
	for i := 0; i < len(audioResponse); i += chunkSize {
		end := i + chunkSize
		if end > len(audioResponse) {
			end = len(audioResponse)
		}
		stream.Send(&pb.AiResponse{
			Type:      pb.AiResponse_TTS_AUDIO,
			AudioData: audioResponse[i:end],
		})
	}
	slog.Info("Initial greeting sent", "session_id", sessionID)
}

// sendCushionPhrase: 대기 안내 메시지 송출
func (s *Server) sendCushionPhrase(stream pb.VoicebotAiService_StreamSessionServer) {
	stream.Send(&pb.AiResponse{
		Type:        pb.AiResponse_STT_RESULT,
		TextContent: "...", // 인식 중 표시
	})
	
	// 실제 환경에서는 고성능 쿠션 오디오 파일을 로드하여 송출함
	stream.Send(&pb.AiResponse{
		Type:        pb.AiResponse_TTS_AUDIO,
		TextContent: config.AppConfig.OpenAI.CushionMsg,
		AudioData:   make([]byte, 3200), // 가짜 정적 오디오 (테스트용)
	})
}

// processAIResponse: STT -> LLM -> TTS 파이프라인 실행
func (s *Server) processAIResponse(stream pb.VoicebotAiService_StreamSessionServer, sessionID string, pcmData []byte) {
	ctx := context.Background()

	slog.Info("Starting AI pipeline", "session_id", sessionID, "pcm_size", len(pcmData))

	// Step 1: STT (WAV 헤더 추가 필수)
	wavData := AddWAVHeader(pcmData)
	recognizedText, err := s.engine.Transcribe(ctx, wavData)
	if err != nil {
		slog.Error("STT failed", "session_id", sessionID, "err", err)
		return
	}
	if recognizedText == "" {
		slog.Warn("STT result is empty", "session_id", sessionID)
		return
	}
	slog.Info("STT Result Success", "session_id", sessionID, "text", recognizedText)
	
	stream.Send(&pb.AiResponse{
		Type:        pb.AiResponse_STT_RESULT,
		TextContent: recognizedText,
	})

	// Step 2: LLM
	slog.Info("Requesting LLM response", "session_id", sessionID)
	responseText, err := s.engine.GenerateResponse(ctx, recognizedText)
	if err != nil {
		slog.Error("LLM failed", "session_id", sessionID, "err", err)
		return
	}
	slog.Info("LLM Response Success", "session_id", sessionID, "response", responseText)

	// Step 3: TTS
	slog.Info("Synthesizing TTS audio", "session_id", sessionID)
	raw24kAudio, err := s.engine.Synthesize(ctx, responseText)
	if err != nil {
		slog.Error("TTS failed", "session_id", sessionID, "err", err)
		return
	}

	// OpenAI 24kHz -> Bridge 16kHz Resampling
	audioResponse := Resample24To16(raw24kAudio)
	slog.Info("TTS Synthesis Success", "session_id", sessionID, "original_size", len(raw24kAudio), "resampled_size", len(audioResponse))

	// Step 4: 스트리밍 응답 (PCM 데이터 브릿지 규격인 20ms 단위로 쪼개서 전송)
	// 16kHz Mono 16bit = 32000 bytes/sec. 20ms = 640 bytes.
	chunkSize := 640
	for i := 0; i < len(audioResponse); i += chunkSize {
		end := i + chunkSize
		if end > len(audioResponse) {
			end = len(audioResponse)
		}
		
		stream.Send(&pb.AiResponse{
			Type:      pb.AiResponse_TTS_AUDIO,
			AudioData: audioResponse[i:end],
		})
	}

	// 전송 완료 신호
	stream.Send(&pb.AiResponse{
		Type: pb.AiResponse_END_OF_TURN,
	})
	slog.Info("AI Turn completed and audio sent", "session_id", sessionID)
}


