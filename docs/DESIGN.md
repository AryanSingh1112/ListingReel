# ListingReel: design notes & production path

## 1. Problem restated
A sales team has *structured facts* + *photos* per listing and no time to cut a video per property.
Success = **enter details + images → coherent MP4 with zero manual editing**, robust to messy input, and **never says anything untrue**
(a false claim in a property ad is a legal and trust problem, which is why grounding is the centrepiece rather than "make it sound nice").

## 2. Requirements → design mapping
| Brief requirement | Design answer |
|---|---|
| Form or CLI for details + images | CLI (`generate`, `doctor`, `--dry-run`) and Streamlit form share `pipeline.generate()` |
| Script + captions from AI or clear template; **only supplied facts** | Fact sheet with ids → template/LLM → **grounding verifier** (numbers, proper nouns, claim words, vocabulary, price exactness, provenance) |
| Scenes, readable overlays, transitions, optional narration/music | Planner (beat-snapped) + compositor (adaptive scrim, WCAG contrast) + procedural royalty-free music + TTS + ducking |
| MP4 + preview/save | H.264/AAC MP4, poster, storyboard, SRT; UI preview + download |
| Graceful missing images/details | Degradation ladder (see below); typed errors with hints |

### Degradation ladder
```
photos:   ≥4 good ─► use as is
          1-3     ─► reuse as "detail" crops (alt-saliency region, tighter zoom) + warning
          bad/dup ─► skipped individually with reason
          0       ─► branded gradient cards (accent from hero) + warning
details:  no price ─► price scene omitted      no contact ─► CTA text only
          no feats ─► title + price + CTA       long text  ─► shortened + warning
audio:    no Piper ─► espeak-ng ─► silent edit (reading-speed timing) ─► captions still carry content
LLM:      error/invalid/hallucinated ─► template
duration: > 40 s ─► drop last features   < 20 s ─► add whole beats to holds
```

## 3. Why not the obvious approaches?
* **moviepy / ffmpeg filtergraph strings**: slow, hard to test, jittery `zoompan`. Frame-level numpy rendering is deterministic, unit-testable, and memory-flat.
* **LLM-only scripting**: non-deterministic, costs money, can invent amenities ("near metro"). Kept as an *optional, verified* mode.
* **Stock music**: licensing and attribution risk, and cuts can't align with it. Procedural score is free, unlimited and beat-aligned.
* **Fixed scene durations**: either cut narration off or leave dead air. Audio-driven timing + beat snap avoids both.

## 4. Architecture properties
* **Pure, typed stages** (`pydantic` models between stages) → each stage unit-testable; the only side effects are ffmpeg/TTS subprocesses and file writes.
* **Stateless & deterministic** → horizontally scalable; same input ⇒ byte-identical plan; safe to retry.
* **Fail-soft, fail-loud**: user input problems → `InputError` with hint (exit 2); missing system dep → `DependencyError` (3); encoder failure → `RenderError` (4). Our own template failing grounding is an internal error, not silently shipped.
* **Observability built in**: `report.json` holds per-stage facts (timings, scores, scrim, contrast, QA, warnings, AI usage).
* **Security/privacy**: no network by default; secrets only via env; subprocess calls use argument lists (no shell); text never reaches a shell; image decode errors caught; synthetic sample data only.

## 5. Production roadmap
1. **Service**: wrap `generate()` in FastAPI (`POST /jobs` multipart → job id, `GET /jobs/{id}`), Celery/RQ or SQS workers, S3 for inputs/outputs, one render per container (CPU only; measured ~4× real-time at 1080×1920).
2. **Throughput**: encode with `-preset veryfast` for drafts; split scenes across processes and concatenate; GPU `h264_nvenc` where available; cache decoded/analysed images by content hash.
3. **Quality**: Piper/cloud neural TTS; CLIP-based room classification to auto-order scenes and name them; face/plate blur for privacy; brand kits (logo, fonts, palette, end-card) per agency; multilingual templates (Hindi/Marathi/etc.).
4. **Growth loop**: render 2-3 variants (different hook/music/pace) per listing, track click-through, learn which structure converts.
5. **Governance**: store `script.json` provenance with each video for audit; configurable claim-lexicon per jurisdiction (RERA wording, mandatory disclaimers).
6. **CI**: `pytest` + a golden-frame test (SSIM against a reference storyboard) to catch visual regressions.
