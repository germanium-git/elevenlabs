# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

`pdf2practice.py` converts Linguahouse-style vocabulary PDFs into MP3 practice audio using the ElevenLabs TTS API. It strips Czech translations, category labels, and page furniture, then synthesises English sentences with configurable pauses for shadowing/repeat practice.

## Setup

```bash
pip install pdfplumber requests pydub imageio-ffmpeg
export ELEVENLABS_API_KEY=sk_xxx
```

## Common commands

```bash
# Preview what will be spoken (no API key needed)
python pdf2practice.py file.pdf --dry-run

# Recommended two-step: extract to editable text, then synthesise
python pdf2practice.py file.pdf --save-txt file.txt
python pdf2practice.py file.txt --repeat 2 --speed 0.8 --rotate-voices -o practice.mp3

# One-shot: stitch mode (one API call per sentence, supports voice rotation)
python pdf2practice.py file.pdf --rotate-voices -o practice.mp3

# One-shot: breaks mode (single API call, cheapest, 3 s pause cap)
python pdf2practice.py file.pdf --mode breaks -o practice.mp3
```

## Architecture

The script has three logical sections:

**PDF extraction** (`extract_units`): Uses `pdfplumber` to pull raw text, then applies regex rules to reflow wrapped lines, drop Czech/metadata lines, strip category labels while keeping the sentence after them, and stop at "Word Families"/"Study Tips" sections. Headword lines like `autocratic (adj.)` are kept as spoken cues (strippable with `--no-headwords`).

**Synthesis** (`synth`, `build_stitch`, `build_breaks`):
- `synth()` hits the ElevenLabs `/v1/text-to-speech/{voice_id}` endpoint and caches results by SHA-1 of `(model|voice|speed|text)` in `~/.pdf2practice_cache/`. Re-runs only pay for changed content.
- `build_stitch`: one API call per sentence/word, concatenated with `pydub` silence. Supports voice rotation (`VOICES` list) and exact pause lengths.
- `build_breaks`: single API call with SSML `<break time="xs"/>` tags between units. Faster/cheaper but pauses cap at 3 s and only one voice.

**CLI** (`main`): `argparse`-based. The global `VOICES` list (top of file) is the default rotation pool; `--voice` overrides it at runtime.

## Key constants to know

- `VOICES` list (line ~61): default ElevenLabs voice IDs for rotation
- `MODEL_ID` (line ~69): `eleven_multilingual_v2` by default; `eleven_flash_v2_5` is cheaper
- `CACHE_DIR`: `~/.pdf2practice_cache`
- `LABEL` regex: category prefixes stripped from sentence content
- `DROP` regexes: lines removed entirely (Czech `CZ:`, page numbers, section banners)
- `STOP` regex: stops extraction at "Word Families" or "Study Tips"

## Alternative input: AI extraction

`vocabulary_extraction_prompt.txt` contains a prompt for using an AI (e.g. Claude) to extract vocabulary from a PDF into a structured `.txt` file. The resulting file can be fed directly to `pdf2practice.py` as a text input.
