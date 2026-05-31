import asyncio
import grpc
import sys
from pathlib import Path

# PYTHONPATH 설정
sys.path.insert(0, "/app")
from app.grpc_stubs.voicebot import voicebot_pb2 as pb
from app.grpc_stubs.voicebot import voicebot_pb2_grpc as pb_grpc

async def connect_session(sid):
    async with grpc.aio.insecure_channel("localhost:50051") as ch:
        stub = pb_grpc.VoicebotAiServiceStub(ch)
        async def request_iter():
            yield pb.AudioChunk(session_id=sid, audio_data=b"\x00"*640, is_speaking=True)
            await asyncio.sleep(5)  # 5초간 연결 유지

        metadata = (("x-tenant-id", "default"),)
        call = stub.StreamSession(request_iter(), metadata=metadata)
        try:
            async for resp in call:
                print(f"[{sid}] Response: {resp.text_content}")
                if "ERROR" in resp.text_content:
                    return "REFUSED"
        except Exception as e:
            return str(e)
    return "FINISHED"

async def main():
    print("Starting session-1...")
    task1 = asyncio.create_task(connect_session("duplicate-id"))
    await asyncio.sleep(1)  # 첫 세션이 Lock을 잡을 때까지 대기
    
    print("Starting session-2 (same ID)...")
    result2 = await connect_session("duplicate-id")
    print(f"Session-2 result: {result2}")
    
    await task1

if __name__ == "__main__":
    asyncio.run(main())
