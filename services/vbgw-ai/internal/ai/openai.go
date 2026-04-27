package ai

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"vbgw-ai/internal/config"

	"github.com/sashabaranov/go-openai"
)

type OpenAIProvider struct {
	client *openai.Client
	config *config.Config
}

func NewOpenAIProvider(cfg *config.Config) *OpenAIProvider {
	return &OpenAIProvider{
		client: openai.NewClient(cfg.OpenAI.APIKey),
		config: cfg,
	}
}

// Transcribe: PCM 오디오 데이터를 Whisper MP3/WAV 형식 세그먼트로 변환하여 텍스트 추출
func (p *OpenAIProvider) Transcribe(ctx context.Context, audioData []byte) (string, error) {
	if len(audioData) == 0 {
		return "", nil
	}

	// OpenAI API는 파일 형식을 요구하므로 메모리 버퍼를 파일처럼 래핑합니다.
	// Whisper API용 더미 파일명 제공 (확장자가 중요함)
	reader := bytes.NewReader(audioData)
	
	req := openai.AudioRequest{
		Model:    p.config.OpenAI.STTModel,
		FilePath: "input.wav", // 실제 파일이 아닌 형식 힌트
		Reader:   reader,
	}

	resp, err := p.client.CreateTranscription(ctx, req)
	if err != nil {
		return "", fmt.Errorf("OpenAI STT error: %w", err)
	}

	return resp.Text, nil
}

// GenerateResponse: GPT-4o를 이용한 응답 생성
func (p *OpenAIProvider) GenerateResponse(ctx context.Context, prompt string) (string, error) {
	req := openai.ChatCompletionRequest{
		Model: p.config.OpenAI.LLMModel,
		Messages: []openai.ChatCompletionMessage{
			{
				Role:    openai.ChatMessageRoleSystem,
				Content: "당신은 지능형 음성봇 고객 응대 상담원입니다. 친절하고 간결하게 응답하세요.",
			},
			{
				Role:    openai.ChatMessageRoleUser,
				Content: prompt,
			},
		},
		MaxTokens: 500,
	}

	resp, err := p.client.CreateChatCompletion(ctx, req)
	if err != nil {
		return "", fmt.Errorf("OpenAI LLM error: %w", err)
	}

	if len(resp.Choices) == 0 {
		return "죄송합니다. 말씀을 이해하지 못했습니다.", nil
	}

	return resp.Choices[0].Message.Content, nil
}

// Synthesize: 텍스트를 PCM 데이터로 변환 (브릿지 전달용)
func (p *OpenAIProvider) Synthesize(ctx context.Context, text string) ([]byte, error) {
	req := openai.CreateSpeechRequest{
		Model:          openai.SpeechModel(p.config.OpenAI.TTSModel),
		Input:          text,
		Voice:          openai.SpeechVoice(p.config.OpenAI.TTSVoice),
		ResponseFormat: openai.SpeechResponseFormatPcm, // Raw PCM 16kHz 16bit Mono
	}

	resp, err := p.client.CreateSpeech(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("OpenAI TTS error: %w", err)
	}
	defer resp.Close()

	return io.ReadAll(resp)
}

