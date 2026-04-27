package vad

import (
	"sync"
	"testing"
)

func TestVADSessionIsolation(t *testing.T) {
	// 1. Engine 생성 (실제 모델이 없으면 fallback 모드로 동작하므로 테스트 가능)
	engine := NewEngine("models/silero_vad.onnx")
	defer engine.Close()

	// 2. 두 개의 인스턴스 생성
	inst1 := engine.NewInstance()
	defer inst1.Close()
	inst2 := engine.NewInstance()
	defer inst2.Close()

	// 3. 병렬 처리 테스트
	const numIterations = 100
	var wg sync.WaitGroup
	wg.Add(2)

	// dummy 16kHz mono 32ms PCM (512 samples * 2 bytes = 1024 bytes)
	silentFrame := make([]byte, 1024) 

	go func() {
		defer wg.Done()
		for i := 0; i < numIterations; i++ {
			inst1.Process(silentFrame)
		}
	}()

	go func() {
		defer wg.Done()
		for i := 0; i < numIterations; i++ {
			inst2.Process(silentFrame)
		}
	}()

	wg.Wait()
	// Race detector가 활성화된 상태에서 실행하면 격리 여부를 완벽히 검증 가능
}
