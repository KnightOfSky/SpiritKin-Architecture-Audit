from backend.agents.base import AgentContext
from backend.executors.base import ExecutionRequest, ExecutionResult
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.decision_cache import CachedDecision, DecisionCache
from backend.tools import ExecutionTool, ToolRegistry, ToolSpec


class _FakeExecutor:
    def __init__(self, name: str, supported_targets: set[str]):
        self.name = name
        self._supported_targets = supported_targets
        self.requests: list[ExecutionRequest] = []

    def supports(self, request):
        return request.target in self._supported_targets

    def execute(self, request):
        self.requests.append(request)
        return ExecutionResult(success=True, message=f"fake executed: {request.target}.{request.operation}")


def _cache(tmp_path, **kwargs):
    path = tmp_path / "decision_cache_state.json"
    return DecisionCache(path=path, **kwargs)


def test_disabled_by_default_env(monkeypatch):
    monkeypatch.delenv("SPIRITKIN_DECISION_CACHE_ENABLED", raising=False)
    cache = DecisionCache(path="runtime/never_written.json")
    assert cache.enabled is False


def test_disabled_cache_is_noop(tmp_path):
    cache = _cache(tmp_path, enabled=False, min_success=1)
    fp = cache.fingerprint("打开记事本", channel="desktop", agent="intent")
    cache.record_success(fp, target="app", operation="launch_app", params={"app_name": "notepad"})
    # Nothing persisted, nothing hittable, no file created.
    assert cache.lookup(fp) is None
    assert not (tmp_path / "decision_cache_state.json").exists()


def test_fingerprint_normalizes_whitespace_and_case(tmp_path):
    cache = _cache(tmp_path, enabled=True, min_success=1)
    a = cache.fingerprint("  打开  Notepad  ", channel="Desktop", agent="Intent")
    b = cache.fingerprint("打开 notepad", channel="desktop", agent="intent")
    assert a == b


def test_fingerprint_distinguishes_channel_and_agent(tmp_path):
    cache = _cache(tmp_path, enabled=True, min_success=1)
    base = cache.fingerprint("打开记事本", channel="desktop", agent="intent")
    other_channel = cache.fingerprint("打开记事本", channel="voice", agent="intent")
    other_agent = cache.fingerprint("打开记事本", channel="desktop", agent="general")
    assert base != other_channel
    assert base != other_agent


def test_hit_requires_min_success_threshold(tmp_path):
    cache = _cache(tmp_path, enabled=True, min_success=2)
    fp = cache.fingerprint("打开记事本", channel="desktop", agent="intent")
    cache.record_success(fp, target="app", operation="launch_app", params={"app_name": "notepad"})
    assert cache.lookup(fp) is None  # only 1 success, threshold is 2
    cache.record_success(fp, target="app", operation="launch_app", params={"app_name": "notepad"})
    hit = cache.lookup(fp)
    assert isinstance(hit, CachedDecision)
    assert hit.target == "app"
    assert hit.operation == "launch_app"
    assert hit.params == {"app_name": "notepad"}
    assert hit.success_count == 2


def test_more_recent_failure_blocks_hit(tmp_path):
    cache = _cache(tmp_path, enabled=True, min_success=1)
    fp = cache.fingerprint("打开记事本", channel="desktop", agent="intent")
    cache.record_success(fp, target="app", operation="launch_app", params={"app_name": "notepad"})
    assert cache.lookup(fp) is not None
    cache.record_failure(fp)
    assert cache.lookup(fp) is None


def test_success_after_failure_becomes_hittable_again(tmp_path):
    cache = _cache(tmp_path, enabled=True, min_success=1)
    fp = cache.fingerprint("打开记事本", channel="desktop", agent="intent")
    cache.record_success(fp, target="app", operation="launch_app", params={"app_name": "notepad"})
    cache.record_failure(fp)
    assert cache.lookup(fp) is None
    cache.record_success(fp, target="app", operation="launch_app", params={"app_name": "notepad"})
    assert cache.lookup(fp) is not None


def test_missing_target_or_operation_not_recorded(tmp_path):
    cache = _cache(tmp_path, enabled=True, min_success=1)
    fp = cache.fingerprint("noise", channel="desktop", agent="intent")
    cache.record_success(fp, target="", operation="launch_app", params={})
    cache.record_success(fp, target="app", operation="", params={})
    assert cache.lookup(fp) is None


def test_persistence_across_instances(tmp_path):
    fp_input = ("打开记事本", "desktop", "intent")
    first = _cache(tmp_path, enabled=True, min_success=1)
    fp = first.fingerprint(*fp_input)
    first.record_success(fp, target="app", operation="launch_app", params={"app_name": "notepad"})
    second = _cache(tmp_path, enabled=True, min_success=1)
    assert second.lookup(fp) is not None


def test_env_min_success_parsing(monkeypatch, tmp_path):
    monkeypatch.setenv("SPIRITKIN_DECISION_CACHE_ENABLED", "true")
    monkeypatch.setenv("SPIRITKIN_DECISION_CACHE_MIN_SUCCESS", "3")
    cache = DecisionCache(path=tmp_path / "s.json")
    assert cache.enabled is True
    fp = cache.fingerprint("x", channel="c", agent="a")
    for _ in range(2):
        cache.record_success(fp, target="app", operation="launch_app", params={})
    assert cache.lookup(fp) is None
    cache.record_success(fp, target="app", operation="launch_app", params={})
    assert cache.lookup(fp) is not None


def test_empty_fingerprint_is_never_hittable(tmp_path):
    cache = _cache(tmp_path, enabled=True, min_success=1)
    assert cache.lookup("") is None
    cache.record_success("", target="app", operation="launch_app", params={})
    assert cache.lookup("") is None


def _high_risk_cluster(tmp_path, *, executor):
    registry = ToolRegistry()
    registry.register(ExecutionTool(ToolSpec("danger.do", "危险操作", "danger", "do", risk_level="high")))
    cache = _cache(tmp_path, enabled=True, min_success=1)
    cluster = AgentCluster(
        llm_client=lambda _: "{}",
        executors=[executor],
        tool_registry=registry,
        decision_cache=cache,
    )
    return cluster, cache


def test_cache_hit_on_high_risk_still_requires_confirmation(tmp_path):
    executor = _FakeExecutor("danger", {"danger"})
    cluster, cache = _high_risk_cluster(tmp_path, executor=executor)
    context = AgentContext(user_input="执行危险操作", metadata={"input_channel": "desktop"})
    fp = cache.fingerprint("执行危险操作", channel="desktop", agent="intent")
    cache.record_success(fp, target="danger", operation="do", params={})

    reply = cluster._handle_intent_fallback(context)

    assert reply is not None
    assert reply.metadata["response_kind"] == "confirmation_request"
    assert reply.requires_confirmation is True
    # Cache hit must NOT bypass the confirmation boundary — executor never ran.
    assert executor.requests == []


def test_cache_hit_marks_metadata_flag(tmp_path):
    executor = _FakeExecutor("safe", {"safe"})
    registry = ToolRegistry()
    registry.register(ExecutionTool(ToolSpec("safe.do", "安全操作", "safe", "do", risk_level="low")))
    cache = _cache(tmp_path, enabled=True, min_success=1)
    cluster = AgentCluster(
        llm_client=lambda _: "{}",
        executors=[executor],
        tool_registry=registry,
        decision_cache=cache,
    )
    context = AgentContext(user_input="做安全操作", metadata={"input_channel": "desktop"})
    fp = cache.fingerprint("做安全操作", channel="desktop", agent="intent")
    cache.record_success(fp, target="safe", operation="do", params={})

    reply = cluster._handle_intent_fallback(context)

    assert reply is not None
    assert reply.metadata.get("decision_cache_hit") is True
    assert executor.requests and executor.requests[-1].target == "safe"


def test_disabled_cache_never_short_circuits_resolver(tmp_path):
    executor = _FakeExecutor("safe", {"safe"})
    registry = ToolRegistry()
    registry.register(ExecutionTool(ToolSpec("safe.do", "安全操作", "safe", "do", risk_level="low")))
    cache = _cache(tmp_path, enabled=False, min_success=1)
    resolver_calls = {"count": 0}

    def _llm(_prompt):
        resolver_calls["count"] += 1
        return "{}"

    cluster = AgentCluster(
        llm_client=_llm,
        executors=[executor],
        tool_registry=registry,
        decision_cache=cache,
    )
    context = AgentContext(user_input="做安全操作", metadata={"input_channel": "desktop"})
    fp = cache.fingerprint("做安全操作", channel="desktop", agent="intent")
    # Even if we try to seed, disabled cache stores nothing and never hits.
    cache.record_success(fp, target="safe", operation="do", params={})

    reply = cluster._handle_intent_fallback(context)

    # Disabled: no cache hit, resolver LLM path was consulted instead.
    assert resolver_calls["count"] >= 1
    if reply is not None:
        assert reply.metadata.get("decision_cache_hit") is not True
