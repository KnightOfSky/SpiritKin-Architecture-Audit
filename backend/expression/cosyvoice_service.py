from __future__ import annotations

import argparse
import io
import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    text: str
    voice_id: str
    language: str = "zh-CN"
    model_name: str = Field(default="", alias="model")
    reference_audio: str
    reference_text: str
    format: str = "wav"


class _ServiceState:
    model: Any = None
    model_dir: Path | None = None
    profile_root: Path | None = None
    inference_lock = threading.Lock()


state = _ServiceState()
app = FastAPI(title="SpiritKin Local CosyVoice", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": state.model is not None,
        "provider": "cosyvoice",
        "model_dir": str(state.model_dir or ""),
        "loopback_only": True,
    }


@app.post("/v1/synthesize")
def synthesize(request: SynthesisRequest) -> Response:
    if state.model is None or state.profile_root is None:
        raise HTTPException(status_code=503, detail="model_not_ready")
    if request.format.lower() != "wav":
        raise HTTPException(status_code=400, detail="unsupported_format")
    text = request.text.strip()
    reference_text = request.reference_text.strip()
    if not text or not reference_text:
        raise HTTPException(status_code=400, detail="text_and_reference_text_required")

    reference = Path(request.reference_audio).expanduser().resolve()
    if not reference.is_file() or not reference.is_relative_to(state.profile_root):
        raise HTTPException(status_code=403, detail="reference_audio_outside_profile_root")

    model_prompt_text = reference_text
    if type(state.model).__name__ == "CosyVoice3" and "<|endofprompt|>" not in model_prompt_text:
        model_prompt_text = f"You are a helpful assistant.<|endofprompt|>{model_prompt_text}"

    chunks: list[np.ndarray] = []
    with state.inference_lock:
        for output in state.model.inference_zero_shot(text, model_prompt_text, str(reference), stream=False):
            speech = output.get("tts_speech")
            if speech is None:
                continue
            chunks.append(speech.detach().cpu().float().numpy().reshape(-1))
    if not chunks:
        raise HTTPException(status_code=500, detail="empty_synthesis")

    audio = np.concatenate(chunks)
    buffer = io.BytesIO()
    sf.write(buffer, audio, state.model.sample_rate, format="WAV", subtype="PCM_16")
    return Response(content=buffer.getvalue(), media_type="audio/wav")


def _load_reference_wav(path: Path, target_sample_rate: int):
    import torch
    import torchaudio.functional as audio_functional

    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    speech = torch.from_numpy(mono).unsqueeze(0)
    if int(sample_rate) != int(target_sample_rate):
        speech = audio_functional.resample(speech, int(sample_rate), int(target_sample_rate))
    return speech


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the loopback-only SpiritKin CosyVoice service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50000)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--profile-root", required=True)
    parser.add_argument("--cosyvoice-root", required=True)
    parser.add_argument("--fp16", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("CosyVoice service must bind to a loopback address")

    cosyvoice_root = Path(args.cosyvoice_root).expanduser().resolve()
    matcha_root = cosyvoice_root / "third_party" / "Matcha-TTS"
    sys.path.insert(0, str(cosyvoice_root))
    sys.path.insert(0, str(matcha_root))

    from cosyvoice.cli.cosyvoice import AutoModel
    import cosyvoice.cli.frontend as cosyvoice_frontend

    state.model_dir = Path(args.model_dir).expanduser().resolve()
    state.profile_root = Path(args.profile_root).expanduser().resolve()
    cosyvoice_frontend.load_wav = _load_reference_wav
    state.model = AutoModel(model_dir=str(state.model_dir), fp16=bool(args.fp16))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
