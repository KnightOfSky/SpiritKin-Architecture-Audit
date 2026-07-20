# Voice Identity and AI Cover Plan

Date: 2026-07-18

Status update (2026-07-18): `spiritkin.primary.v1` is now the selected local
assistant profile, using the isolated mechanical-female reference line selected
by the owner. The profile, transcript, provenance restrictions, and SHA-256 are
stored under the ignored `state/voice-profiles/` root. Runtime configuration
prefers the loopback-only CosyVoice adapter and explicitly reports/falls back to
Edge-TTS while the isolated service is offline. The isolated Python 3.10,
PyTorch 2.9.1 cu128, allowlisted Fun-CosyVoice3 model files, loopback service,
and the first repeatable WAV preview are installed locally. Runtime now reports
`cosyvoice` as the active backend while port 50000 is healthy.

Status update (2026-07-19): the desktop launcher now owns the CosyVoice
loopback service lifecycle. `python scripts/start_desktop_console.py
--restart-wpf` starts or reuses the service only when the selected profile and
model assets are present, the endpoint is loopback-only, and
`tts.provider=cosyvoice`; `--status` reports the configured/ready state and
managed PID. `SPIRITKIN_AUTOSTART_COSYVOICE=0` disables autostart, while the
`SPIRITKIN_COSYVOICE_*` variables provide explicit local-path overrides. A
managed cold-start smoke test generated a 24 kHz, 2.48 second WAV at
`state/run/cosyvoice-managed-start-smoke.wav`. The iOS controller therefore
uses the same selected local profile instead of a separately named mobile
voice.

## Product Boundary

- Speech synthesis and singing voice conversion are separate pipelines. A TTS
  provider should not be stretched into a singing engine.
- Android remains a Bridge/execution endpoint and does not own voice identity,
  3D Avatar rendering, training, or music production.
- Desktop/runtime owns audio jobs and model execution. iOS is the mobile
  controller for preview, approval, job status, and export.
- Only voices with recorded permission and songs with suitable usage rights may
  enter training, conversion, or export. Every generated file keeps provenance.

## 1. Specified Speech Voice

The current runtime already supports `edge_tts` and `pyttsx3`. A named stock
voice can be selected immediately through `SPIRITKIN_TTS_VOICE` or
`tts.voice` in `config/config.yaml`.

For a custom, stable SpiritKin voice, add a consented zero-shot provider as an
isolated audio service. CosyVoice is the preferred first provider because it
supports multilingual and cross-lingual zero-shot synthesis. Keep its Python,
PyTorch, and optional vLLM dependencies outside the core runtime environment.

Each custom voice must have a versioned profile:

```json
{
  "voice_id": "spiritkin.primary.v1",
  "display_name": "SpiritKin Primary",
  "speech_provider": "cosyvoice",
  "speech_model": "Fun-CosyVoice3-0.5B",
  "reference_audio": "state/voice-profiles/spiritkin.primary.v1/reference.wav",
  "reference_text": "The exact transcript of the reference audio.",
  "language": "zh-CN",
  "allowed_uses": ["assistant_speech"],
  "consent_record": "state/voice-profiles/spiritkin.primary.v1/consent.json",
  "created_at": "ISO-8601 timestamp"
}
```

Do not store voice samples or consent records in Git. Store hashes and profile
metadata in the audit log; keep the source audio under the state root.

## 2. AI Cover Pipeline

The first production path is singing voice conversion, not text-to-song:

1. **Ingest and rights gate**: accept a local song, target voice profile, key
   shift, and an explicit rights/consent record.
2. **Stem separation**: split vocals and accompaniment. A pinned Demucs build
   is sufficient for the first local pipeline, but it is archived and must be
   isolated behind a provider interface.
3. **Vocal preparation**: normalize sample rate, trim silence, extract pitch
   with RMVPE, and preserve timing and melody.
4. **Singing voice conversion**: use RVC for the practical first version. Add
   Amphion/Vevo2 later for higher-control SVC/SVS experiments without changing
   the job contract.
5. **Mix and master**: align converted vocals, apply gain/EQ/de-essing as
   configured, mix with the instrumental, and run FFmpeg loudness analysis.
6. **Review and export gate**: create a preview first. Final WAV/FLAC/MP3 export
   and any publication require explicit human approval.

## 3. Runtime Contract

Audio jobs should be asynchronous and resumable:

```text
queued -> validating -> separating -> converting -> mixing -> review_required
       -> approved -> exported
       -> failed / canceled
```

Recommended endpoints/tools:

- `voice.profile.list`, `voice.profile.validate`
- `voice.synthesize` with `voice_id`, text, language, rate, output format
- `cover.create` with song path, target `voice_id`, pitch shift, and rights id
- `cover.preview`, `cover.approve`, `cover.export`, `cover.cancel`

Artifacts include the original input hash, model/profile versions, command
parameters, intermediate stem hashes, output loudness, reviewer, and approval
timestamp. Raw reference audio is never returned by general artifact APIs.

## 4. Delivery Slices

### V1 - Voice Selection

- Keep Edge-TTS as the fallback and add audition/export for selected stock
  voices.
- Select one `voice_id` as the SpiritKin default after listening review.
- Expose the selected profile and synthesis job status in desktop and iOS.

### V2 - Custom Speech Voice

- Deploy CosyVoice in an isolated local service.
- Add reference-audio validation, transcript matching, consent metadata, and
  deterministic profile versioning.
- Route normal assistant speech through the selected profile with Edge-TTS
  fallback when the custom provider is offline.

### V3 - AI Cover

- Add source separation, RMVPE, RVC conversion, FFmpeg mixing, preview, and
  approval-gated export.
- Add a dedicated iOS job view for progress and A/B preview; Android only
  transfers approved artifacts when a workflow requires it.

### V4 - Research Upgrade

- Evaluate Amphion/Vevo2 for higher-quality SVC/SVS and controllable singing.
- Keep the same voice profile, job state, provenance, and approval contracts so
  the research model can be replaced without rewriting product workflows.

## Acceptance

- A speech profile produces repeatable output from the same text and settings.
- Fallback never silently changes identity; the UI and artifact metadata name
  the provider actually used.
- Cover previews preserve timing and melody without clipping or missing stems.
- Training and conversion refuse missing consent/rights metadata.
- Export records model, voice profile, source hashes, reviewer, and approval.
- No Android screen or package contains Avatar or model-training UI.
