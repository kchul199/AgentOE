# Agentic Callbot Project Rules
- Performance First: 모든 I/O 작업(STT, LLM 호출, DB 등)은 철저하게 비동기(Async/Await) 기반의 non-blocking으로 작성할 것.
- Latency is King: 실시간 콜봇이므로 불필요한 루프나 지연을 유발하는 코드는 절대 금지.
- Error Handling: 에이전트 도구 호출(Tool Calling) 실패 시, 고객의 통화가 끊기지 않도록 우아한 Fallback 시나리오를 반드시 구현할 것.
