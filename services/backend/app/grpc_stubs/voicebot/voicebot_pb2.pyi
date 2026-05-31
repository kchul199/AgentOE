from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper

DESCRIPTOR: _descriptor.FileDescriptor

class AudioChunk(_message.Message):
    __slots__ = ("audio_data", "dtmf_digit", "is_speaking", "session_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    AUDIO_DATA_FIELD_NUMBER: _ClassVar[int]
    IS_SPEAKING_FIELD_NUMBER: _ClassVar[int]
    DTMF_DIGIT_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    audio_data: bytes
    is_speaking: bool
    dtmf_digit: str
    def __init__(
        self,
        session_id: str | None = ...,
        audio_data: bytes | None = ...,
        is_speaking: bool = ...,
        dtmf_digit: str | None = ...,
    ) -> None: ...

class AiResponse(_message.Message):
    __slots__ = ("audio_data", "clear_buffer", "text_content", "type")
    class ResponseType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STT_RESULT: _ClassVar[AiResponse.ResponseType]
        TTS_AUDIO: _ClassVar[AiResponse.ResponseType]
        END_OF_TURN: _ClassVar[AiResponse.ResponseType]

    STT_RESULT: AiResponse.ResponseType
    TTS_AUDIO: AiResponse.ResponseType
    END_OF_TURN: AiResponse.ResponseType
    TYPE_FIELD_NUMBER: _ClassVar[int]
    TEXT_CONTENT_FIELD_NUMBER: _ClassVar[int]
    AUDIO_DATA_FIELD_NUMBER: _ClassVar[int]
    CLEAR_BUFFER_FIELD_NUMBER: _ClassVar[int]
    type: AiResponse.ResponseType
    text_content: str
    audio_data: bytes
    clear_buffer: bool
    def __init__(
        self,
        type: AiResponse.ResponseType | str | None = ...,
        text_content: str | None = ...,
        audio_data: bytes | None = ...,
        clear_buffer: bool = ...,
    ) -> None: ...
