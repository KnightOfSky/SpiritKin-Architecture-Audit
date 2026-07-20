from unittest.mock import patch

from backend.mobile.ios_conversation import handle_ios_direct_chat, should_use_ios_direct_chat


def test_direct_chat_accepts_conversation_but_not_runtime_actions():
    assert should_use_ios_direct_chat("你好，简单介绍一下你自己") is True
    assert should_use_ios_direct_chat("打开电商 Terminal") is False
    assert should_use_ios_direct_chat("检查桌面端运行状态") is False
    assert should_use_ios_direct_chat("你好", {"full_runtime": True}) is False


def test_direct_chat_uses_bounded_non_reasoning_generation():
    with patch("backend.mobile.ios_conversation.get_llm_response", return_value="你好，我在。") as model:
        reply = handle_ios_direct_chat(
            "你好",
            {
                "workspace_id": "tenant-a",
                "max_new_tokens": 48,
                "model_timeout_seconds": 12,
            },
        )

    assert reply.text == "你好，我在。"
    assert reply.metadata["ios_direct_chat"] is True
    assert reply.metadata["workspace_id"] == "tenant-a"
    assert model.call_args.kwargs["max_new_tokens"] == 48
    assert model.call_args.kwargs["reasoning_effort"] == "none"
    assert model.call_args.kwargs["request_timeout"] == 12


def test_direct_chat_includes_bounded_session_context_and_clamps_client_tuning():
    sessions = {
        "sessions": [
            {
                "id": "session-a",
                "messages": [
                    {"role": "user", "text": "我喜欢简洁回答"},
                    {"role": "assistant", "text": "记住了。"},
                    {"role": "user", "text": "那刚才那个呢"},
                ],
            }
        ]
    }
    with (
        patch("backend.mobile.ios_sessions.ios_sessions_snapshot", return_value=sessions),
        patch("backend.mobile.ios_conversation.get_llm_response", return_value="它也会简洁处理。") as model,
    ):
        handle_ios_direct_chat(
            "那刚才那个呢",
            {
                "workspace_id": "tenant-a",
                "session_id": "session-a",
                "max_new_tokens": 9999,
                "model_timeout_seconds": 9999,
                "reasoning_effort": "high",
            },
        )

    prompt = model.call_args.args[0]
    assert "我喜欢简洁回答" in prompt
    assert prompt.count("那刚才那个呢") == 1
    assert model.call_args.kwargs["max_new_tokens"] == 128
    assert model.call_args.kwargs["request_timeout"] == 90
    assert model.call_args.kwargs["reasoning_effort"] == "none"
