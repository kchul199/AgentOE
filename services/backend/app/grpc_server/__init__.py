"""gRPC server module — VoicebotAiService implementation for vbgw bridge."""
from app.grpc_server.server import GrpcServerLifecycle, build_server

__all__ = ["GrpcServerLifecycle", "build_server"]
