module github.com/kchul199/agentoe/services/vbgw-ai

go 1.26.2

require (
	github.com/joho/godotenv v1.5.1
	github.com/kchul199/agentoe/contracts/gen/go v0.0.0-00010101000000-000000000000
	github.com/sashabaranov/go-openai v1.41.2
	google.golang.org/grpc v1.81.1
)

require (
	golang.org/x/net v0.51.0 // indirect
	golang.org/x/sys v0.42.0 // indirect
	golang.org/x/text v0.34.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260226221140-a57be14db171 // indirect
	google.golang.org/protobuf v1.36.11 // indirect
)

replace github.com/kchul199/agentoe/contracts/gen/go => ../../contracts/gen/go
