"""Provider-neutral shared model runtime (PRD §9; issues #6, #36, #37, #38).

Public surface:

    from harness.model import FakeModelClient, build_model_client

Agent roles depend only on the ``ModelClient`` protocol and the value types
in :mod:`harness.model.types`; provider wiring happens once via
:func:`build_model_client`, so swapping providers changes no agent code.
"""

from harness.model.config import (
    ModelClientSettings,
    ProviderName,
    build_model_client,
    resolve_capabilities,
    settings_from_env,
)
from harness.model.errors import (
    AuthenticationError,
    CapabilityNotSupportedError,
    ContextWindowExceededError,
    InvalidRequestError,
    MalformedModelError,
    ModelConfigurationError,
    ModelError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    http_status_to_error,
)
from harness.model.fake import (
    FakeModelClient,
    FakeScriptExhaustedError,
    RecordedCall,
    ScriptedTurn,
)
from harness.model.providers.anthropic import AnthropicClient
from harness.model.providers.http import (
    ProviderHttpRequest,
    ProviderHttpResponse,
    urllib_json_transport,
)
from harness.model.providers.openai_compatible import OpenAICompatibleClient
from harness.model.redaction import (
    SECRET_PLACEHOLDER,
    Redactor,
    Secret,
    truncate_for_message,
)
from harness.model.types import (
    FinishReason,
    Message,
    ModelCapabilities,
    ModelClient,
    ModelResponse,
    ResponseSchema,
    Role,
    ToolCall,
    ToolSpec,
    Usage,
    estimate_message_tokens,
    estimate_tokens,
)

__all__ = [
    "SECRET_PLACEHOLDER",
    "AnthropicClient",
    "AuthenticationError",
    "CapabilityNotSupportedError",
    "ContextWindowExceededError",
    "FakeModelClient",
    "FakeScriptExhaustedError",
    "FinishReason",
    "InvalidRequestError",
    "MalformedModelError",
    "Message",
    "ModelCapabilities",
    "ModelClient",
    "ModelClientSettings",
    "ModelConfigurationError",
    "ModelError",
    "ModelResponse",
    "OpenAICompatibleClient",
    "ProviderHttpRequest",
    "ProviderHttpResponse",
    "ProviderName",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RateLimitError",
    "RecordedCall",
    "Redactor",
    "ResponseSchema",
    "Role",
    "ScriptedTurn",
    "Secret",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "build_model_client",
    "estimate_message_tokens",
    "estimate_tokens",
    "http_status_to_error",
    "resolve_capabilities",
    "settings_from_env",
    "truncate_for_message",
    "urllib_json_transport",
]
