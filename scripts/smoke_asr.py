from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.agent_cluster_ports import DefaultAgentClusterAppPort
from backend.app.experience_loop import verify_feishu_experience_loop
from backend.app.runtime import SpiritKinRuntime
from backend.app.settings import (
    resolve_asr_compute_type,
    resolve_asr_device,
    resolve_asr_model_size,
    resolve_asr_profile,
    resolve_hotword,
)
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.planner import Planner
from backend.perception.audio.listener import (
    LOCAL_ASR_MODEL_DIR,
    AsrModelUnavailableError,
    calibrate_microphone,
    get_whisper_model,
    listen_from_microphone,
    listen_from_microphone_with_metrics,
    resolve_asr_model_selection,
)

DEFAULT_MODELSCOPE_IDS = {
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def _offline_llm(prompt: str) -> str:
    raise RuntimeError("ASR smoke 接入飞书闭环时不应退回通用 LLM")


def _print_agent_reply(label: str, reply) -> None:
    if reply is None:
        print(f"\n# {label}\n[FAIL] 智能体没有返回结果")
        return

    print(f"\n# {label}")
    print(f"agent={reply.agent_name} emotion={reply.emotion} action={reply.action} requires_confirmation={reply.requires_confirmation}")
    print(f"text={reply.text}")
    if reply.spoken_text:
        print(f"spoken_text={reply.spoken_text}")

    intent = reply.metadata.get("intent_resolution") if isinstance(reply.metadata, dict) else None
    if intent:
        print("\n## 意图智能体")
        print(f"status={intent.get('status')} source={intent.get('source')} confidence={intent.get('confidence')} reason={intent.get('reason')}")
        if intent.get("corrected_text"):
            print(f"corrected_text={intent.get('corrected_text')}")

    if reply.requires_confirmation:
        print("\n## 确认门")
        print(f"pending_target={reply.metadata.get('pending_target')} pending_operation={reply.metadata.get('pending_operation')}")

    execution = reply.metadata.get("execution") if isinstance(reply.metadata, dict) else None
    if execution:
        print("\n## 执行器")
        print(f"target={execution.get('target')} operation={execution.get('operation')} success={execution.get('success')}")
        if execution.get("data") is not None:
            print(f"data={execution.get('data')}")
        if execution.get("error"):
            print(f"error={execution.get('error')}")


def _print_asr_config(*, allow_fallback: bool) -> dict[str, object]:
    profile = resolve_asr_profile()
    selection = resolve_asr_model_selection(allow_fallback=allow_fallback)
    print("# ASR 配置")
    print(f"model_size={resolve_asr_model_size()}")
    print(
        f"selected_model={selection['selected']} cached={selection['cached']} "
        f"fallback={selection['fallback']} available={selection['available']}"
    )
    print(f"device={resolve_asr_device()} compute_type={resolve_asr_compute_type()}")
    print(f"beam_size={profile.get('beam_size')} vad_filter={profile.get('vad_filter')} temperature={profile.get('temperature')}")
    print(f"allow_download={os.getenv('SPIRIT_ALLOW_MODEL_DOWNLOAD', '') or '0'}")
    print(f"hf_endpoint={os.getenv('HF_ENDPOINT', '') or '默认 HuggingFace'}")
    print(f"proxy={os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or '未设置'}")
    return selection


def _looks_like_feishu_send_request(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return Planner._build_feishu_request(normalized, text or "") is not None


def _normalize_voice_trigger_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", (text or "").strip()).lower()


def _is_hotword_only(text: str, hotword: str) -> bool:
    normalized_text = _normalize_voice_trigger_text(text)
    normalized_hotword = _normalize_voice_trigger_text(hotword)
    return bool(normalized_text and normalized_hotword and normalized_text == normalized_hotword)


def _strip_hotword_prefix(text: str, hotword: str) -> tuple[str, bool]:
    raw_text = (text or "").strip()
    raw_hotword = (hotword or "").strip()
    if not raw_text or not raw_hotword:
        return raw_text, False

    pattern = rf"^{re.escape(raw_hotword)}[\s,，。.!！?？:：、-]*"
    stripped = re.sub(pattern, "", raw_text, count=1, flags=re.IGNORECASE).strip()
    if stripped and stripped != raw_text:
        return stripped, True
    return raw_text, False


def _listen_and_print_asr_result(label: str, *, timeout: int, phrase_time_limit: int) -> str | None:
    result = listen_from_microphone_with_metrics(timeout=timeout, phrase_time_limit=phrase_time_limit)
    text = result.get("text")
    print(f"\n# {label}")
    print(
        f"elapsed={float(result.get('elapsed') or 0.0):.2f}s "
        f"listen={float(result.get('listen_elapsed') or 0.0):.2f}s "
        f"transcribe={float(result.get('transcribe_elapsed') or 0.0):.2f}s"
    )
    if result.get("rejected_segments"):
        print(f"rejected_segments={result.get('rejected_segments')}")
    if result.get("error"):
        print(f"error={result.get('error')}")
    print(f"text={text!r}")
    return text


def _print_model_unavailable_help(selection: dict[str, object]) -> None:
    requested = selection["requested"]
    print(f"\n[FAIL] 请求的 ASR 模型 faster-whisper-{requested} 本地没有缓存。")
    print("现在不会再先监听再偷偷降级到 base；请先选一种方式：")
    print("1) 下载高质量模型：$env:SPIRIT_ALLOW_MODEL_DOWNLOAD='1'; python scripts/smoke_asr.py --route-feishu")
    print("2) 临时接受本地低质量模型：python scripts/smoke_asr.py --route-feishu --allow-fallback")
    print("3) 明确改用本地模型：$env:SPIRIT_ASR_MODEL_SIZE='base'; python scripts/smoke_asr.py --route-feishu")


def _print_model_load_error_help(error: Exception, selection: dict[str, object]) -> None:
    requested = selection["requested"]
    print(f"\n[FAIL] ASR 模型 faster-whisper-{requested} 准备失败。")
    print(f"原因：{error}")
    print("\n这通常是 HuggingFace 连接超时/被阻断，不是麦克风问题。可选处理：")
    print("1) 使用 HuggingFace 镜像后重试：")
    print("   $env:HF_ENDPOINT='https://hf-mirror.com'")
    print("   $env:SPIRIT_ALLOW_MODEL_DOWNLOAD='1'")
    print("   python scripts/smoke_asr.py --route-feishu")
    print("2) 如果只是验证流程，可临时回退本地模型：")
    print("   python scripts/smoke_asr.py --route-feishu --allow-fallback")
    print("3) 如果你已手动下载模型，把目录放到：")
    print(f"   backend/models/asr/faster-whisper-{requested}")


def _prepare_download_environment(*, use_hf_mirror: bool) -> None:
    allow_download = os.getenv("SPIRIT_ALLOW_MODEL_DOWNLOAD", "").strip().lower() in {"1", "true", "yes", "on"}
    if use_hf_mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ.setdefault("SPIRIT_ALLOW_MODEL_DOWNLOAD", "1")
        return

    if allow_download and not os.getenv("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        print("[ℹ️] 已自动使用 HuggingFace 镜像：HF_ENDPOINT=https://hf-mirror.com")


def _apply_proxy(proxy: str | None) -> None:
    if not proxy:
        return
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["http_proxy"] = proxy
    os.environ["https_proxy"] = proxy


def _find_local_proxy() -> str | None:
    candidates = (7890, 7897, 10809, 10808, 20171, 2080, 8080)
    for port in candidates:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return None


def _apply_auto_proxy() -> bool:
    proxy = _find_local_proxy()
    if not proxy:
        return False
    _apply_proxy(proxy)
    print(f"[ℹ️] 已自动发现本地代理：{proxy}")
    return True


def _check_model_download_route(model_size: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    endpoint = (os.getenv("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    url = f"{endpoint}/api/models/Systran/faster-whisper-{model_size}"
    request = urllib.request.Request(url, headers={"User-Agent": "SpiritKinAI-ASR-Smoke/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            response.read(128)
        if 200 <= status < 400:
            return True, f"可访问 {url}"
        return False, f"HTTP {status}: {url}"
    except Exception as exc:
        return False, f"无法访问 {url}: {exc}"


def _check_modelscope_route(model_id: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    url = f"https://www.modelscope.cn/api/v1/models/{model_id}"
    request = urllib.request.Request(url, headers={"User-Agent": "SpiritKinAI-ASR-Smoke/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            response.read(128)
        if 200 <= status < 400:
            return True, f"可访问 ModelScope: {model_id}"
        return False, f"HTTP {status}: {url}"
    except Exception as exc:
        return False, f"无法访问 ModelScope {url}: {exc}"


def _download_from_modelscope(model_size: str, model_id: str) -> Path:
    import_errors: list[str] = []
    try:
        from modelscope import snapshot_download
    except Exception as exc:
        import_errors.append(f"from modelscope import snapshot_download -> {type(exc).__name__}: {exc}")
        try:
            from modelscope.hub.snapshot_download import snapshot_download
        except Exception as fallback_exc:
            import_errors.append(
                "from modelscope.hub.snapshot_download import snapshot_download "
                f"-> {type(fallback_exc).__name__}: {fallback_exc}"
            )
            raise RuntimeError(
                "当前 Python 环境无法导入 ModelScope 下载接口。请在当前 conda 环境安装/修复 modelscope。"
                f"\n导入错误：{' | '.join(import_errors)}"
            ) from fallback_exc

    target_dir = Path(LOCAL_ASR_MODEL_DIR) / f"faster-whisper-{model_size}"
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[⬇️] 正在从 ModelScope 下载 {model_id}")
    print(f"[⬇️] 保存到 {target_dir}")
    snapshot_download(model_id=model_id, local_dir=str(target_dir))
    return target_dir


def _print_network_route_help(message: str) -> None:
    print("\n[FAIL] 模型下载地址预检失败，还没开始下载。")
    print(f"原因：{message}")
    print("\n下一步请二选一：")
    print("1) 如果你有 Clash/V2Ray 代理，直接一条命令：")
    print("   python scripts/smoke_asr.py --route-feishu --hf-mirror --auto-proxy")
    print("   或指定实际端口：python scripts/smoke_asr.py --route-feishu --hf-mirror --proxy http://127.0.0.1:你的端口")
    print("2) 如果没有代理，只能先手动把模型下载到：")
    print("   backend/models/asr/faster-whisper-large-v3-turbo")
    print("3) 或直接尝试国内 ModelScope：")
    print("   python scripts/smoke_asr.py --route-feishu --modelscope")


def main() -> int:
    parser = argparse.ArgumentParser(description="麦克风 ASR 实机 smoke，并可把识别文本接入飞书闭环")
    parser.add_argument("--timeout", type=int, default=8, help="等待开始说话的秒数")
    parser.add_argument("--phrase-time-limit", type=int, default=6, help="单句话最长录音秒数；长消息可手动调大")
    parser.add_argument("--route-feishu", action="store_true", help="把 ASR 结果送入飞书 dry-run 闭环验证")
    parser.add_argument("--route-agent", action="store_true", help="把 ASR 结果送入完整语音智能体链路，由 LLM 意图智能体纠错/理解/执行")
    parser.add_argument("--agent-voice-intent-mode", default="always", help="语音智能体意图解析模式：always/first/fallback/off，--route-agent 默认 always")
    parser.add_argument("--hotword", default="", help="route-agent 时用于过滤的唤醒词，默认读取 SPIRIT_HOTWORD/config 或 Spirit")
    parser.add_argument("--hotword-retries", type=int, default=2, help="route-agent 只听到唤醒词时，继续重听具体指令的次数")
    parser.add_argument("--allow-fallback", action="store_true", help="允许请求模型缺失时回退到本地低质量模型")
    parser.add_argument("--hf-mirror", action="store_true", help="使用 https://hf-mirror.com 下载 HuggingFace 模型")
    parser.add_argument("--proxy", default="", help="设置下载代理，例如 http://127.0.0.1:7890")
    parser.add_argument("--auto-proxy", action="store_true", help="自动扫描常见本地代理端口并用于模型下载")
    parser.add_argument("--modelscope", action="store_true", help="从 ModelScope 下载 CTranslate2 ASR 模型到本地目录")
    parser.add_argument("--modelscope-id", default="", help="覆盖 ModelScope 模型 ID，例如 mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    parser.add_argument("--check-network-only", action="store_true", help="只检查模型下载地址/代理是否可达，不加载模型")
    parser.add_argument("--skip-network-check", action="store_true", help="跳过下载前网络预检")
    parser.add_argument("--no-preload", action="store_true", help="跳过监听前模型预热，仅用于定位问题")
    parser.add_argument("--no-calibrate", action="store_true", help="跳过监听前环境噪声校准；默认会短校准以减少空噪声误触发")
    parser.add_argument("--calibrate-duration", type=float, default=0.6, help="监听前环境噪声校准秒数，默认 0.6")
    parser.add_argument("--visual-context", default="ASR 实机 smoke，无真实视觉输入", help="接入闭环时使用的视觉上下文")
    args = parser.parse_args()

    if args.route_feishu and args.route_agent:
        print("[FAIL] --route-feishu 和 --route-agent 是两种 smoke 入口，请二选一。")
        return 2

    if args.modelscope:
        os.environ.setdefault("SPIRIT_ALLOW_MODEL_DOWNLOAD", "1")
    if args.auto_proxy and not args.proxy:
        if not _apply_auto_proxy():
            print("[ℹ️] 未发现常见本地代理端口：7890/7897/10809/10808/20171/2080/8080")
    _apply_proxy(args.proxy)
    _prepare_download_environment(use_hf_mirror=args.hf_mirror)

    selection = _print_asr_config(allow_fallback=args.allow_fallback)
    if not selection["available"]:
        _print_model_unavailable_help(selection)
        return 2

    needs_download = bool(selection["available"] and not selection["cached"] and not selection["local_only"])
    if needs_download and args.modelscope:
        selected = str(selection["selected"])
        model_id = args.modelscope_id or DEFAULT_MODELSCOPE_IDS.get(selected, f"Systran/faster-whisper-{selected}")
        if not args.skip_network_check:
            print("\n[🌐] 正在预检 ModelScope 下载地址...")
            ok, message = _check_modelscope_route(model_id)
            if not ok:
                _print_network_route_help(message)
                return 2
            print(f"[OK] {message}")
        if args.check_network_only:
            return 0
        try:
            _download_from_modelscope(selected, model_id)
        except Exception as exc:
            print(f"\n[FAIL] ModelScope 下载失败：{exc}")
            return 2
        selection = _print_asr_config(allow_fallback=args.allow_fallback)
        needs_download = bool(selection["available"] and not selection["cached"] and not selection["local_only"])

    if needs_download and not args.skip_network_check:
        print("\n[🌐] 正在预检模型下载地址...")
        ok, message = _check_model_download_route(str(selection["selected"]))
        if not ok:
            _print_network_route_help(message)
            return 2
        print(f"[OK] {message}")
    if args.check_network_only:
        return 0

    if not args.no_preload:
        print("\n[🤔] 正在监听前预热 ASR 模型，避免录音后才加载...")
        preload_started = time.perf_counter()
        try:
            get_whisper_model(allow_fallback=args.allow_fallback)
        except AsrModelUnavailableError as exc:
            _print_model_load_error_help(exc, selection)
            return 2
        print(f"[OK] ASR 模型已就绪，preload_elapsed={time.perf_counter() - preload_started:.2f}s")

    if not args.no_calibrate:
        print("\n[🎚️] 正在短校准环境噪声，尽量避免没说话也触发...")
        calibrate_started = time.perf_counter()
        calibrate_microphone(duration=max(0.1, args.calibrate_duration))
        print(f"[OK] 麦克风校准完成，calibrate_elapsed={time.perf_counter() - calibrate_started:.2f}s")

    if args.route_agent:
        print("\n请自然说一句指令，例如：机械B现在怎么装它 / 帮我把飞书开一下 / 给张三发个消息说我晚点到")
    else:
        print("\n请说一句，例如：给张三发飞书，说会议改到三点")
    text = _listen_and_print_asr_result("ASR 结果", timeout=args.timeout, phrase_time_limit=args.phrase_time_limit)

    if args.route_agent:
        hotword = resolve_hotword(args.hotword or None)
        retry_count = 0
        max_hotword_retries = max(0, args.hotword_retries)
        while text and _is_hotword_only(text, hotword) and retry_count < max_hotword_retries:
            retry_count += 1
            print(f"[↩️] 只识别到唤醒词“{text}”，不会送入智能体。请直接说具体指令（第 {retry_count}/{max_hotword_retries} 次重听）。")
            text = _listen_and_print_asr_result(
                f"ASR 结果（唤醒后重听 {retry_count}）",
                timeout=args.timeout,
                phrase_time_limit=args.phrase_time_limit,
            )
        if text and _is_hotword_only(text, hotword):
            print(f"[FAIL] 只识别到唤醒词“{text}”，没有拿到具体指令")
            return 1
        if text:
            stripped_text, stripped_hotword = _strip_hotword_prefix(text, hotword)
            if stripped_hotword:
                print(f"[↩️] 已消费句首唤醒词“{hotword}”，实际送入智能体：{stripped_text!r}")
                text = stripped_text

    if not text:
        print("[FAIL] 没有识别到有效文本")
        return 1

    if not args.route_feishu and not args.route_agent:
        print("[PASS] ASR 已输出文本。若要继续验证完整智能体链路，加 --route-agent")
        return 0

    if args.route_agent:
        os.environ.setdefault("SPIRIT_FEISHU_DRY_RUN", "1")
        os.environ.setdefault("SPIRIT_FEISHU_CONTACTS_JSON", '{"张三":"user_id:demo_zhangsan"}')
        cluster = AgentCluster(
            voice_intent_mode=args.agent_voice_intent_mode,
            app_port=DefaultAgentClusterAppPort(),
        )
        runtime = SpiritKinRuntime(agent=cluster, emit_runtime_events=False)
        reply = runtime.handle_voice_input(text, visual_context=args.visual_context)
        _print_agent_reply("语音智能体结果", reply)

        if reply is not None and reply.requires_confirmation:
            print("\n请继续说：确认执行 / 取消执行")
            confirm_started = time.perf_counter()
            confirm_text = listen_from_microphone(timeout=args.timeout, phrase_time_limit=max(4, min(args.phrase_time_limit, 8)))
            print(f"\n# 确认语音 ASR 结果\nelapsed={time.perf_counter() - confirm_started:.2f}s\ntext={confirm_text!r}")
            if not confirm_text:
                print("[FAIL] 没有识别到确认/取消文本")
                return 1
            confirm_reply = runtime.handle_voice_input(confirm_text, visual_context=args.visual_context)
            _print_agent_reply("确认后智能体结果", confirm_reply)
            reply = confirm_reply

        execution = reply.metadata.get("execution") if reply is not None and isinstance(reply.metadata, dict) else None
        if execution and execution.get("success") is False:
            return 1
        return 0 if reply is not None else 1

    if not _looks_like_feishu_send_request(text):
        print("[FAIL] ASR 输出不是“发送飞书消息”请求，已停止路由，避免误触发打开应用或通用 LLM。")
        print("可试试：帮我跟张三说会议改到三点 / 用飞书通知张三，说会议改到三点 / 发消息给张三，内容是会议改到三点")
        return 1

    os.environ["SPIRIT_FEISHU_DRY_RUN"] = "1"
    os.environ.setdefault("SPIRIT_FEISHU_CONTACTS_JSON", '{"张三":"user_id:demo_zhangsan"}')
    runtime = SpiritKinRuntime(agent=AgentCluster(llm_client=_offline_llm), emit_runtime_events=False)
    report = verify_feishu_experience_loop(runtime, transcript=text, visual_context=args.visual_context)
    print("\n" + report.to_markdown())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
