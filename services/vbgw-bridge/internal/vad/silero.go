//go:build cgo

/**
 * @file silero.go
 * @description Silero VAD v4 ONNX 추론 엔진 — onnxruntime-go 바인딩
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | energy-based stub
 * v1.1.0 | 2026-04-07 | [Implementer] | Phase 2 | 실제 ONNX 추론 구현 (cgo 빌드 태그)
 * v1.2.0 | 2026-04-09 | [Implementer] | T-01 | infer() 입력 텐서 업데이트 수정 + ORT_LIB_PATH 환경변수
 * ─────────────────────────────────────────
 */

package vad

import (
	"log/slog"
	"os"

	ort "github.com/yalue/onnxruntime_go"
)

const (
	// vadThreshold is the probability threshold for speech detection.
	vadThreshold = 0.5

	// sampleRate for Silero VAD v4.
	sampleRate = 16000
)

// Engine handles the shared ONNX model and environment.
type Engine struct {
	modelPath string
	session   *ort.DynamicAdvancedSession
	isV4      bool
}

// Instance manages per-session VAD state (LSTM hidden states and buffers).
type Instance struct {
	engine *Engine
	buffer []int16

	// Per-session tensors for parallel inference
	input  *ort.Tensor[float32]
	h      *ort.Tensor[float32]
	c      *ort.Tensor[float32]
	sr     *ort.Tensor[int64]
	output *ort.Tensor[float32]
	hn     *ort.Tensor[float32]
	cn     *ort.Tensor[float32]
}

// NewEngine creates a shared VAD engine.
func NewEngine(modelPath string) *Engine {
	ort.SetSharedLibraryPath(getOrtLibPath())
	if err := ort.InitializeEnvironment(); err != nil {
		slog.Error("ONNX env init failed", "err", err)
		return &Engine{modelPath: modelPath}
	}

	e := &Engine{modelPath: modelPath}
	if err := e.loadModel(); err != nil {
		slog.Error("VAD model load failed", "path", modelPath, "err", err)
	} else {
		slog.Info("VAD engine initialized (Shared Model)", "path", modelPath)
	}
	return e
}

func (e *Engine) loadModel() error {

	options, err := ort.NewSessionOptions()
	if err == nil {
		options.SetIntraOpNumThreads(1)
		options.SetInterOpNumThreads(1)
	}

	// Try current version Silero VAD v4/v5 inputs
	e.session, err = ort.NewDynamicAdvancedSession(
		e.modelPath,
		[]string{"input", "sr", "h", "c"},
		[]string{"output", "hn", "cn"},
		options,
	)
	if err == nil {
		e.isV4 = true
	} else {
		slog.Warn("VAD v4/v5 init failed, trying v3/simple mode", "err", err)
		// Fallback for older or simplified models
		e.session, err = ort.NewDynamicAdvancedSession(
			e.modelPath,
			[]string{"input"},
			[]string{"output"},
			options,
		)
		e.isV4 = false
	}
	return err
}

// NewInstance creates a per-session VAD instance.
func (e *Engine) NewInstance() *Instance {
	inst := &Instance{
		engine: e,
		buffer: make([]int16, 0, vadWindowSamples*2),
	}

	if e.session == nil {
		return inst
	}

	// Allocate per-instance tensors
	var err error
	inst.input, err = ort.NewEmptyTensor[float32](ort.NewShape(1, vadWindowSamples))
	inst.output, err = ort.NewEmptyTensor[float32](ort.NewShape(1, 1))

	if e.isV4 {
		inst.sr, err = ort.NewTensor(ort.NewShape(1), []int64{sampleRate})
		inst.h, err = ort.NewEmptyTensor[float32](ort.NewShape(2, 1, 64))
		inst.c, err = ort.NewEmptyTensor[float32](ort.NewShape(2, 1, 64))
		inst.hn, err = ort.NewEmptyTensor[float32](ort.NewShape(2, 1, 64))
		inst.cn, err = ort.NewEmptyTensor[float32](ort.NewShape(2, 1, 64))
	}

	if err != nil {
		slog.Error("Failed to allocate VAD instance tensors", "err", err)
		return inst
	}

	return inst
}

// Process takes raw PCM bytes and returns speech detection result.
// No global mutex: state is isolated within the Instance.
func (inst *Instance) Process(pcmBytes []byte) bool {
	samples := bytesToInt16(pcmBytes)
	inst.buffer = append(inst.buffer, samples...)

	var isSpeaking bool
	for len(inst.buffer) >= vadWindowSamples {
		window := inst.buffer[:vadWindowSamples]
		inst.buffer = inst.buffer[vadWindowSamples:]

		if inst.engine.session != nil {
			isSpeaking = inst.infer(window)
		} else {
			isSpeaking = energyDetectFallback(window)
		}
	}
	return isSpeaking
}

func (inst *Instance) infer(samples []int16) bool {
	if inst.engine.session == nil || inst.input == nil {
		return energyDetectFallback(samples)
	}

	// Normalize and write to input tensor
	inputSlice := inst.input.GetData()
	for i, s := range samples {
		inputSlice[i] = float32(s) / 32768.0
	}

	// Propagate hidden states
	copy(inst.h.GetData(), inst.hn.GetData())
	copy(inst.c.GetData(), inst.cn.GetData())

	// Run inference binding this instance's tensors
	var inputs []ort.ArbitraryTensor
	var outputs []ort.ArbitraryTensor

	if inst.h != nil && inst.c != nil && inst.hn != nil && inst.cn != nil {
		// Silero VAD v4/v5 (with state tensors)
		inputs = []ort.ArbitraryTensor{inst.input, inst.sr, inst.h, inst.c}
		outputs = []ort.ArbitraryTensor{inst.output, inst.hn, inst.cn}
	} else {
		// Silero VAD v3 or simple mode
		inputs = []ort.ArbitraryTensor{inst.input}
		outputs = []ort.ArbitraryTensor{inst.output}
	}

	if err := inst.engine.session.Run(inputs, outputs); err != nil {
		slog.Error("VAD inference failed", "err", err)
		return energyDetectFallback(samples)
	}

	prob := inst.output.GetData()[0]
	return prob > vadThreshold
}

func (inst *Instance) Close() {
	if inst.input != nil { inst.input.Destroy() }
	if inst.h != nil { inst.h.Destroy() }
	if inst.c != nil { inst.c.Destroy() }
	if inst.sr != nil { inst.sr.Destroy() }
	if inst.output != nil { inst.output.Destroy() }
	if inst.hn != nil { inst.hn.Destroy() }
	if inst.cn != nil { inst.cn.Destroy() }
}

func (e *Engine) Close() {
	if e.session != nil {
		e.session.Destroy()
	}
	ort.DestroyEnvironment()
}


// energyDetectFallback is used when ONNX load fails.
func energyDetectFallback(samples []int16) bool {
	if len(samples) == 0 {
		return false
	}
	var sum int64
	for _, s := range samples {
		if s < 0 {
			sum -= int64(s)
		} else {
			sum += int64(s)
		}
	}
	return sum/int64(len(samples)) > 800
}

func getOrtLibPath() string {
	if p := os.Getenv("ORT_LIB_PATH"); p != "" {
		return p
	}
	return "/usr/local/lib/libonnxruntime.so"
}
