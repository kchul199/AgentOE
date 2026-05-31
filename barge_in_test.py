import asyncio
import grpc
import sys
from pathlib import Path

# PYTHONPATH 설정
sys.path.insert(0, "/app")
from app.grpc_stubs.voicebot import voicebot_pb2 as pb
from app.grpc_stubs.voicebot import voicebot_pb2_grpc as pb_grpc

async def test_barge_in():
    async with grpc.aio.insecure_channel("localhost:50051") as ch:
        stub = pb_grpc.VoicebotAiServiceStub(ch)
        
        async def request_iter():
            # 1. 사용자가 먼저 말을 함
            yield pb.AudioChunk(session_id="barge-in-test", audio_data=b"\x00"*640, is_speaking=True)
            await asyncio.sleep(0.1)
            # 2. 발화 멈춤 (AI 처리 유도)
            yield pb.AudioChunk(session_id="barge-in-test", audio_data=b"", is_speaking=False)
            
            # 3. AI가 응답을 주려고 할 때쯤, 사용자가 다시 말을 시작 (Barge-in!)
            await asyncio.sleep(0.5)
            print("[Client] User starts speaking again (Barge-in)...")
            yield pb.AudioChunk(session_id="barge-in-test", audio_data=b"\x00"*640, is_speaking=True)
            await asyncio.sleep(0.5)

        metadata = (("x-tenant-id", "default"),)
        call = stub.StreamSession(request_iter(), metadata=metadata)
        
        async for resp in call:
            if resp.clear_buffer:
                print(f"[Server] Received clear_buffer=True! Barge-in SUCCESS.")
            if resp.type == pb.AiResponse.END_OF_TURN:
                break

if __name__ == "__main__":
    asyncio.run(test_barge_in())
