package ai

import (
	"bytes"
	"encoding/binary"
)

// AddWAVHeader는 Raw PCM 16kHz 16bit Mono 데이터를 Whisper가 인식 가능한 WAV 포맷으로 변환합니다.
func AddWAVHeader(pcmData []byte) []byte {
	size := len(pcmData)
	buf := new(bytes.Buffer)

	// RIFF 헤더
	buf.Write([]byte("RIFF"))
	binary.Write(buf, binary.LittleEndian, uint32(36+size))
	buf.Write([]byte("WAVE"))

	// fmt 청크
	buf.Write([]byte("fmt "))
	binary.Write(buf, binary.LittleEndian, uint32(16))          // Subchunk1Size
	binary.Write(buf, binary.LittleEndian, uint16(1))           // AudioFormat (1 = PCM)
	binary.Write(buf, binary.LittleEndian, uint16(1))           // NumChannels (1 = Mono)
	binary.Write(buf, binary.LittleEndian, uint32(16000))       // SampleRate (16kHz)
	binary.Write(buf, binary.LittleEndian, uint32(16000*2))     // ByteRate (SampleRate * NumChannels * BitsPerSample/8)
	binary.Write(buf, binary.LittleEndian, uint16(2))           // BlockAlign (NumChannels * BitsPerSample/8)
	binary.Write(buf, binary.LittleEndian, uint16(16))          // BitsPerSample

	// data 청크
	buf.Write([]byte("data"))
	binary.Write(buf, binary.LittleEndian, uint32(size))
	buf.Write(pcmData)

	return buf.Bytes()
}

// Resample24To16: OpenAI 24kHz PCM 데이터를 브릿지용 16kHz PCM으로 변환 (3:2 Downsampling)
func Resample24To16(input []byte) []byte {
	// 24kHz -> 16kHz 변환 (단순 선형 보간 또는 3샘플당 2샘플 추출)
	// input은 16bit(2byte) 샘플들의 배열임
	if len(input) < 2 {
		return input
	}

	samples := len(input) / 2
	outputSamples := (samples * 2) / 3
	output := make([]byte, outputSamples*2)

	for i := 0; i < outputSamples; i++ {
		// 16kHz의 i번째 샘플은 24kHz의 (i * 1.5)번째 샘플에 해당함
		idx24 := (i * 3) / 2
		if (idx24*2 + 1) < len(input) {
			output[i*2] = input[idx24*2]
			output[i*2+1] = input[idx24*2+1]
		}
	}

	return output
}

