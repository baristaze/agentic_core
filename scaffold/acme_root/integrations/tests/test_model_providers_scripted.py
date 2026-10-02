"""The scripted provider, the twin of every model provider: it streams each
scripted reply in parts, ending in the reply with its usage, and fails with
every error kind of the table, before a stream or part of the way through
one. No test here reaches a network."""

import pytest

from acme.integrations.model_providers import reply_of
from acme.integrations.model_providers.calls import (
    Finished,
    Message,
    ModelCall,
    ModelReply,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
)
from acme.integrations.model_providers.content import (
    TextBlock,
    ThinkingBlock,
    ThinkingSource,
    ToolUseBlock,
)
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.registry import (
    ModelProvidersOverImpl,
    absent_model_providers,
    scripted_model_providers,
)
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl, ScriptedFailure
from acme.integrations.model_providers.types import (
    ANSWERS,
    ErrorAnswer,
    ErrorKind,
    ProviderName,
    StopReason,
    Usage,
)

CALL = ModelCall(
    model="claude-haiku-4-5",
    messages=(Message(role="user", blocks=(TextBlock(text="Why does the part drop?"),)),),
    max_output_tokens=1024,
)
SOURCE = ThinkingSource(provider=ProviderName.ANTHROPIC, model="claude-haiku-4-5")
REPLY = ModelReply(
    blocks=(
        TextBlock(text="Reading the controller log before anything else."),
        ToolUseBlock(id="toolu_1", name="read_log", input={"path": "/var/log/arm.log"}),
    ),
    thinking=(ThinkingBlock(text="The log first.", signature="sig", source=SOURCE),),
    stop_reason=StopReason.TOOL_USE,
    usage=Usage(input=120, cache_read=900, output=40, thinking=12),
    model="claude-haiku-4-5",
)


async def test_a_reply_streams_in_parts_and_ends_whole_with_its_usage() -> None:
    twin = ModelProviderScriptedImpl(ProviderName.ANTHROPIC, [REPLY])
    parts = [part async for part in twin.stream(CALL)]
    assert isinstance(parts[-1], Finished) and parts[-1].reply == REPLY
    assert parts[-1].reply.usage == Usage(input=120, cache_read=900, output=40, thinking=12)
    texts = [p.text for p in parts if isinstance(p, TextDelta)]
    assert len(texts) > 1 and "".join(texts) == "Reading the controller log before anything else."
    assert "".join(p.text for p in parts if isinstance(p, ThinkingDelta)) == "The log first."
    inputs = [p.partial_input for p in parts if isinstance(p, ToolUseDelta)]
    assert "".join(inputs) == '{"path":"/var/log/arm.log"}'
    assert twin.calls == [CALL] and twin.remaining == 0


async def test_the_same_script_runs_the_same_way_twice() -> None:
    first = ModelProviderScriptedImpl(ProviderName.OPENAI, [REPLY])
    second = ModelProviderScriptedImpl(ProviderName.OPENAI, [REPLY])
    assert [p async for p in first.stream(CALL)] == [p async for p in second.stream(CALL)]


@pytest.mark.parametrize("kind", list(ErrorKind), ids=lambda k: k.value)
async def test_every_error_kind_of_the_table_is_raised(kind: ErrorKind) -> None:
    twin = ModelProviderScriptedImpl(
        ProviderName.ANTHROPIC, [ScriptedFailure(kind=kind, retry_after=2.5, status=500)]
    )
    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(twin.stream(CALL))
    assert failed.value.kind is kind
    assert failed.value.retry_after == 2.5 and failed.value.partial is None


def test_each_kind_has_the_answer_the_table_gives() -> None:
    assert set(ANSWERS) == set(ErrorKind)
    retried = {k for k in ErrorKind if k.answer is ErrorAnswer.RETRY}
    assert retried == {ErrorKind.TRANSIENT, ErrorKind.OVERLOADED, ErrorKind.RATE_LIMITED}
    assert ErrorKind.BILLING.answer is ErrorAnswer.PARK, "a billing error is never retried"
    assert ErrorKind.CREDENTIAL.answer is ErrorAnswer.PARK
    assert ErrorKind.TRANSIENT.answer is not ErrorAnswer.END, "a transient error never ends a loop"
    assert ErrorKind.CONTEXT_OVERFLOW.answer is ErrorAnswer.COMPACT
    assert ErrorKind.MODEL_UNAVAILABLE.answer is ErrorAnswer.RESOLVE
    assert {k for k in ErrorKind if k.answer is ErrorAnswer.END} == {
        ErrorKind.INVALID_REQUEST,
        ErrorKind.PERMANENT,
    }


async def test_a_broken_stream_delivers_what_arrived_and_fails_with_it_truncated() -> None:
    partial = REPLY.model_copy(
        update={"blocks": REPLY.blocks[:1], "stop_reason": None, "truncated": True}
    )
    twin = ModelProviderScriptedImpl(
        ProviderName.ANTHROPIC, [ScriptedFailure(kind=ErrorKind.TRANSIENT, partial=partial)]
    )
    seen: list[object] = []
    with pytest.raises(ModelCallFailed) as failed:
        async for part in twin.stream(CALL):
            seen.append(part)
    assert seen and not any(isinstance(p, Finished) for p in seen)
    assert failed.value.partial == partial and failed.value.partial.truncated


def test_a_reply_with_no_stop_reason_or_cut_by_its_bound_is_never_whole() -> None:
    with pytest.raises(ValueError):
        ModelReply(stop_reason=None, usage=Usage(), model="m")
    with pytest.raises(ValueError):
        ModelReply(stop_reason=StopReason.OUTPUT_LIMIT, usage=Usage(), model="m")
    assert ModelReply(stop_reason=StopReason.OUTPUT_LIMIT, truncated=True, usage=Usage(), model="m")


async def test_a_script_with_no_turn_left_fails_loud() -> None:
    twin = ModelProviderScriptedImpl(ProviderName.OPENAI)
    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(twin.stream(CALL))
    assert failed.value.kind is ErrorKind.PERMANENT


async def test_the_absent_provider_fails_every_call_as_a_missing_credential() -> None:
    providers = absent_model_providers("no key in this process")
    for name in ProviderName:
        with pytest.raises(ModelCallFailed) as failed:
            await reply_of(providers.get(name).stream(CALL))
        assert failed.value.kind is ErrorKind.CREDENTIAL
        assert "no key in this process" in str(failed.value)


def test_the_registry_holds_an_adapter_for_every_provider_and_no_other() -> None:
    twins = scripted_model_providers()
    assert {twins.get(p).provider for p in ProviderName} == set(ProviderName)
    with pytest.raises(ValueError, match="no adapter"):
        ModelProvidersOverImpl({ProviderName.OPENAI: ModelProviderScriptedImpl(ProviderName.OPENAI)})
    with pytest.raises(ValueError, match="another provider"):
        ModelProvidersOverImpl(
            {p: ModelProviderScriptedImpl(ProviderName.OPENAI) for p in ProviderName}
        )
