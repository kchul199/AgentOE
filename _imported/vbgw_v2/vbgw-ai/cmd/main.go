package main

import (
	"fmt"
	"log/slog"
	"net"
	"os"

	"vbgw-ai/internal/ai"
	"vbgw-ai/internal/config"
	pb "vbgw-ai/proto/voicebot"

	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"
)

func main() {
	// 설정 로드
	config.LoadConfig()

	// 1. AI 엔진 인스턴스 생성 (OpenAI)
	engine := ai.NewOpenAIProvider(config.AppConfig)

	port := config.AppConfig.InternalPort
	listenAddr := fmt.Sprintf(":%s", port)
	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		slog.Error("Failed to listen", "port", port, "err", err)
		os.Exit(1)
	}

	s := grpc.NewServer()
	
	// 2. 서버 생성 시 엔진 주입
	aiServer := ai.NewServer(engine)
	pb.RegisterVoicebotAiServiceServer(s, aiServer)

	// Enable reflection for debugging/grpctest
	reflection.Register(s)

	slog.Info("VBGW AI Engine starting with OpenAI", "port", port)
	if err := s.Serve(lis); err != nil {
		slog.Error("Failed to serve gRPC", "err", err)
		os.Exit(1)
	}
}


