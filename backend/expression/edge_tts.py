from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from backend.agents.base import BARE_TAG_PATTERN
from backend.app.settings import resolve_tts_settings


def _hidden_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    kwargs: dict[str, object] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_factory is not None:
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


def clean_speech_text(text: str) -> str:
    value = re.sub(r"<(?:emotion|action):[^>]+>", "", text or "", flags=re.IGNORECASE)
    value = BARE_TAG_PATTERN.sub("", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", value)
    value = re.sub(r"<img\b[^>]*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[（(【\[]\s*(?:笑|微笑|开心|哭|流泪|害羞|尴尬|捂脸|表情|emoji|sticker)[^）)】\]]{0,16}[）)】\]]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\[[^\]]*(?:表情包|表情|图片|image|sticker)[^\]]*\]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r":[a-z0-9_+\-]+:", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[\U0001F000-\U0010FFFF]", " ", value)
    value = re.sub(r"[\u2600-\u27BF\uFE0F\u200D]", " ", value)
    value = re.sub(r"(?:[;:=8xX][\-oO']?[\)\(DPp/\\]|[\)\(][\-oO']?[;:=8xX])", " ", value)
    value = re.sub(r"https?://\S+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"file:/+\S+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\S+\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?\S*)?", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", value) else ""


_SEGMENT_BOUNDARY = re.compile(r"(?<=[\u3002\uff01\uff1f\uff1b!?;\n])")
_SEGMENT_SOFT_BOUNDARY = re.compile(r"(?<=[\uff0c,\u3001])")


def split_speech_segments(text: str, *, min_chars: int = 4, max_chars: int = 64) -> list[str]:
    """Split text into sentence-level segments so synthesis can be pipelined."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    parts: list[str] = []
    for part in _SEGMENT_BOUNDARY.split(cleaned):
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chars:
            parts.append(part)
            continue
        for soft in _SEGMENT_SOFT_BOUNDARY.split(part):
            soft = soft.strip()
            if soft:
                parts.append(soft)
    merged: list[str] = []
    for part in parts:
        if merged and (len(part) < min_chars or len(merged[-1]) < min_chars) and len(merged[-1]) + len(part) <= max_chars:
            merged[-1] += part
        else:
            merged.append(part)
    return merged or [cleaned]


# Persistent PowerShell MediaPlayer: reads mp3 paths from stdin, reports
# "START <seconds>" when playback begins and "DONE" when it ends. Keeping the
# process alive removes the ~1-2s PresentationCore cold start per utterance.
_PLAYER_LOOP_PS = (
    "Add-Type -AssemblyName PresentationCore; "
    "$m = New-Object System.Windows.Media.MediaPlayer; "
    "$m.Volume = 1.0; "
    "while($true){ "
    "$line = [Console]::In.ReadLine(); "
    "if($null -eq $line){ break }; "
    "$line = $line.Trim(); "
    "if($line -eq ''){ continue }; "
    "if($line -eq 'QUIT'){ break }; "
    "try { "
    "$m.Open([Uri]$line); $m.Play(); "
    "$deadline = [DateTime]::UtcNow.AddSeconds(5); "
    "while(!$m.NaturalDuration.HasTimeSpan -and [DateTime]::UtcNow -lt $deadline){ Start-Sleep -Milliseconds 40 }; "
    "$dur = if($m.NaturalDuration.HasTimeSpan){ $m.NaturalDuration.TimeSpan.TotalSeconds } else { 1 }; "
    "[Console]::Out.WriteLine('START ' + $dur.ToString([System.Globalization.CultureInfo]::InvariantCulture)); "
    "[Console]::Out.Flush(); "
    "Start-Sleep -Milliseconds ([Math]::Max(200,[int](($dur + 0.12) * 1000))); "
    "$m.Close() "
    "} catch { "
    "[Console]::Out.WriteLine('START 0'); [Console]::Out.Flush() "
    "}; "
    "[Console]::Out.WriteLine('DONE'); [Console]::Out.Flush() "
    "}"
)


class EdgeTTSProvider:
    def __init__(self, voice: str | None = None, rate: str | None = None):
        settings = resolve_tts_settings()
        self._voice = (voice or settings.voice).strip()
        self._rate = (rate or settings.rate).strip()
        self._stop_event = threading.Event()
        self._process_lock = threading.RLock()
        self._playback_process: subprocess.Popen | None = None

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def rate(self) -> str:
        return self._rate

    def _command_base(self) -> list[str]:
        return ["edge-tts"] if _edge_tts_cli_available() else [sys.executable, "-m", "edge_tts"]

    def stream_speak(self, text: str) -> Iterator[bytes]:
        text = clean_speech_text(text)
        if not text:
            return
        self._stop_event.clear()
        try:
            process = subprocess.Popen(
                [*self._command_base(), "--voice", self._voice, "--rate", self._rate, "--text", text, "--write-media", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                **_hidden_subprocess_kwargs(),
            )
            while True:
                if self._stop_event.is_set():
                    process.terminate()
                    break
                chunk = process.stdout.read(4096) if process.stdout else None
                if not chunk:
                    break
                yield chunk
            process.wait(timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return
        except Exception:
            return

    def speak_to_file(self, text: str, output_path: str | Path) -> bool:
        text = clean_speech_text(text)
        if not text:
            return False
        # In-process synthesis skips the edge-tts CLI interpreter startup
        # (~2-3s per call), which dominates first-audio latency.
        if self._synthesize_in_process(text, output_path):
            return True
        try:
            result = subprocess.run(
                [*self._command_base(), "--voice", self._voice, "--rate", self._rate, "--text", text, "--write-media", str(output_path)],
                capture_output=True, timeout=30, **_hidden_subprocess_kwargs(),
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _synthesize_in_process(self, text: str, output_path: str | Path) -> bool:
        try:
            import asyncio

            import edge_tts
        except Exception:
            return False
        try:
            async def _synthesize() -> None:
                communicate = edge_tts.Communicate(text, self._voice, rate=self._rate)
                await asyncio.wait_for(communicate.save(str(output_path)), timeout=30)

            asyncio.run(_synthesize())
            path = str(output_path)
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception:
            return False

    def speak_and_play(self, text: str, on_segment_start: Callable[[str, float], None] | None = None) -> bool:
        """Speak Chinese text with sentence-level pipelining.

        Each sentence is synthesized while the previous one is playing, so the
        first audio arrives after one short synthesis instead of the whole
        text. `on_segment_start` fires when a segment actually starts playing
        (segment_text, duration_seconds).
        """
        text = clean_speech_text(text)
        if not text:
            return False
        self._stop_event.clear()
        if os.name != "nt":
            tmp_path = os.path.join(tempfile.gettempdir(), f"spiritkin_tts_{int(time.time() * 1000)}.mp3")
            if not self.speak_to_file(text, tmp_path):
                return False
            try:
                if on_segment_start is not None:
                    on_segment_start(text, 0.0)
                os.startfile(tmp_path)  # noqa: S606 - non-nt branch kept for parity
                return True
            except Exception:
                return False
        player = self._ensure_player()
        if player is None:
            if on_segment_start is not None:
                on_segment_start(text, 0.0)
            return self._speak_and_play_single(text)
        segments = split_speech_segments(text)
        synth_queue: queue.Queue[tuple[str, str | None] | None] = queue.Queue(maxsize=2)

        def _producer() -> None:
            try:
                for index, segment in enumerate(segments):
                    if self._stop_event.is_set():
                        break
                    tmp_path = os.path.join(
                        tempfile.gettempdir(), f"spiritkin_tts_{int(time.time() * 1000)}_{index}.mp3"
                    )
                    ok = self.speak_to_file(segment, tmp_path)
                    synth_queue.put((segment, tmp_path if ok else None))
            finally:
                synth_queue.put(None)

        threading.Thread(target=_producer, daemon=True).start()
        played_any = False
        try:
            while True:
                item = synth_queue.get()
                if item is None:
                    break
                segment, tmp_path = item
                if tmp_path is None:
                    continue
                if self._stop_event.is_set():
                    _remove_quietly(tmp_path)
                    continue
                if not self._play_on_player(player, segment, tmp_path, on_segment_start):
                    _remove_quietly(tmp_path)
                    break
                played_any = True
        finally:
            while True:
                try:
                    leftover = synth_queue.get_nowait()
                except queue.Empty:
                    break
                if leftover and leftover[1]:
                    _remove_quietly(leftover[1])
        return played_any and not self._stop_event.is_set()

    def _play_on_player(
        self,
        player: subprocess.Popen,
        segment: str,
        tmp_path: str,
        on_segment_start: Callable[[str, float], None] | None,
    ) -> bool:
        try:
            player.stdin.write(tmp_path + "\n")
            player.stdin.flush()
        except Exception:
            return False
        done = False
        try:
            while True:
                line = player.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("START"):
                    duration_s = 0.0
                    try:
                        duration_s = float(line.split(" ", 1)[1])
                    except (IndexError, ValueError):
                        pass
                    if on_segment_start is not None and not self._stop_event.is_set():
                        try:
                            on_segment_start(segment, duration_s)
                        except Exception:
                            pass
                elif line == "DONE":
                    done = True
                    break
        finally:
            _remove_quietly(tmp_path)
        return done

    def _ensure_player(self) -> subprocess.Popen | None:
        with self._process_lock:
            if self._playback_process is not None and self._playback_process.poll() is None:
                return self._playback_process
            try:
                process = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Sta", "-WindowStyle", "Hidden", "-Command", _PLAYER_LOOP_PS],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace",
                    **_hidden_subprocess_kwargs(),
                )
            except Exception:
                return None
            self._playback_process = process
            return process

    def _speak_and_play_single(self, text: str) -> bool:
        """Legacy one-shot path used when the persistent player cannot start."""
        tmp_path = os.path.join(tempfile.gettempdir(), f"spiritkin_tts_{int(time.time() * 1000)}.mp3")
        if not self.speak_to_file(text, tmp_path):
            print("[tts] edge-tts generation failed")
            return False
        try:
            escaped_path = tmp_path.replace("'", "''")
            ps = (
                f"Add-Type -AssemblyName PresentationCore; "
                f"$m = New-Object System.Windows.Media.MediaPlayer; "
                f"$m.Volume = 1.0; "
                f"$m.Open('{escaped_path}'); $m.Play(); "
                f"$deadline = [DateTime]::UtcNow.AddSeconds(5); "
                f"while(!$m.NaturalDuration.HasTimeSpan -and [DateTime]::UtcNow -lt $deadline){{Start-Sleep -Milliseconds 50}}; "
                f"$dur = if($m.NaturalDuration.HasTimeSpan){{$m.NaturalDuration.TimeSpan.TotalSeconds}}else{{1}}; "
                f"Start-Sleep -Milliseconds ([Math]::Max(250,[int](($dur + 0.2) * 1000))); "
                f"$m.Close()"
            )
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-Sta", "-WindowStyle", "Hidden", "-Command", ps],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **_hidden_subprocess_kwargs(),
            )
            while process.poll() is None:
                if self._stop_event.wait(0.05):
                    process.terminate()
                    return False
            return process.returncode == 0
        except Exception:
            return False
        finally:
            _remove_quietly(tmp_path)

    def stop(self) -> None:
        self._stop_event.set()
        with self._process_lock:
            process = self._playback_process
            self._playback_process = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401

            return True
        except Exception:
            pass
        if _edge_tts_cli_available():
            return True
        try:
            result = subprocess.run([sys.executable, "-m", "edge_tts", "--version"], capture_output=True, timeout=5, **_hidden_subprocess_kwargs())
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


def create_edge_tts_provider(voice: str | None = None, rate: str | None = None) -> EdgeTTSProvider | None:
    provider = EdgeTTSProvider(voice=voice, rate=rate)
    if provider.is_available():
        return provider
    return None


def _edge_tts_cli_available() -> bool:
    try:
        result = subprocess.run(["edge-tts", "--version"], capture_output=True, timeout=5, **_hidden_subprocess_kwargs())
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _remove_quietly(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass
