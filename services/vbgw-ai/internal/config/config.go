package config

import (
	"log"
	"os"

	"github.com/joho/godotenv"
)

type Config struct {
	WSPort       string
	InternalPort string
	AiGrpcAddr   string

	OpenAI struct {
		APIKey     string
		STTModel   string
		LLMModel   string
		TTSModel   string
		TTSVoice   string
		CushionMsg string
		GreetingMsg string
	}
}

var AppConfig *Config

func LoadConfig() {
	// .env 파일 로드 (실패해도 환경 변수가 시스템에 있을 수 있으므로 에러는 무시)
	_ = godotenv.Load()

	AppConfig = &Config{}

	// 서버 포트 설정
	AppConfig.WSPort = getEnv("WS_PORT", "8090")
	AppConfig.InternalPort = getEnv("INTERNAL_PORT", "8091")
	AppConfig.AiGrpcAddr = getEnv("AI_GRPC_ADDR", "vbgw-ai:8091")

	// OpenAI 설정
	AppConfig.OpenAI.APIKey = os.Getenv("OPENAI_API_KEY")
	AppConfig.OpenAI.STTModel = getEnv("OPENAI_STT_MODEL", "whisper-1")
	AppConfig.OpenAI.LLMModel = getEnv("OPENAI_LLM_MODEL", "gpt-4o")
	AppConfig.OpenAI.TTSModel = getEnv("OPENAI_TTS_MODEL", "tts-1")
	AppConfig.OpenAI.TTSVoice = getEnv("OPENAI_TTS_VOICE", "alloy")
	AppConfig.OpenAI.CushionMsg = getEnv("AI_CUSHION_MSG", "잠시만 기다려주세요...")
	AppConfig.OpenAI.GreetingMsg = getEnv("AI_GREETING_MSG", "안녕하세요, 보이스봇입니다. 무엇을 도와드릴까요?")

	if AppConfig.OpenAI.APIKey == "" {
		log.Println("WARNING: OPENAI_API_KEY is not set. Real AI features will fail.")
	}
}

func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}
