#!/usr/bin/env python3
"""
pdf2practice.py
================
Turn a Linguahouse-style vocabulary PDF into a language-practice audio file
using ElevenLabs text-to-speech.

Two-step workflow (recommended)
--------------------------------
  # Step 1 — extract PDF to editable text, review/clean it:
  python pdf2practice.py file.pdf --save-txt file.txt

  # Step 2 — synthesise from the edited text file:
  python pdf2practice.py file.txt --rotate-voices -o out.mp3

One-shot workflow
-----------------
  python pdf2practice.py file.pdf -o practice.mp3
  python pdf2practice.py file.pdf --dry-run   # preview only, no API calls

Two synthesis modes
-------------------
- "stitch" (default): one API call per unit, glued together with real silence.
  => exact pause length (no 3 s cap) AND a different voice per unit/word.
- "breaks": ONE API call for the whole deck, using <break time="x.xs"/> tags.
  => cheapest & fastest, but single voice and pauses capped at 3 s.

Setup
-----
    pip install pdfplumber requests pydub imageio-ffmpeg
    export ELEVENLABS_API_KEY=sk_xxx        # from elevenlabs.io -> Profile -> API Key
"""

import argparse
import hashlib
import io
import os
import re
import sys
import time
from pathlib import Path

import requests

import imageio_ffmpeg
from pydub import AudioSegment
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency: pip install pdfplumber")

# ----------------------------------------------------------------------------- #
# DEFAULT CONFIG  (override most of these from the command line)
# ----------------------------------------------------------------------------- #
API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Voices to rotate through (stitch mode). These are public ElevenLabs voice IDs.
# Swap for your own favourites from elevenlabs.io -> Voices (copy the Voice ID).
VOICES = [
    "21m00Tcm4TlvDq8ikWAM",  # Rachel  (US, female)
    "JBFqnCBsd6RMkjVDRZzb",  # George  (UK, male)
    "XB0fDUnXU5powFXDhCwa",  # Charlotte (English-Swedish, female)
    "onwK4e9ZLuTAKqWW03F9",  # Daniel  (UK, male)
]

# eleven_multilingual_v2 = best quality, supports <break>.  eleven_flash_v2_5 = cheaper/faster.
MODEL_ID = "eleven_multilingual_v2"

DEFAULT_PAUSE_AFTER_UNIT_MS = 1500   # silence after each sentence
DEFAULT_PAUSE_BETWEEN_WORDS_MS = 700 # silence between words (word granularity)
DEFAULT_REPEAT = 1                   # say each unit N times (2 = hear it, then echo it)

CACHE_DIR = Path.home() / ".pdf2practice_cache"   # avoids re-paying for unchanged audio

# ----------------------------------------------------------------------------- #
# CLEANING  (PDF extraction only)
# ----------------------------------------------------------------------------- #
# Labels: strip the "Word:" prefix but keep the sentence after it.
LABEL = re.compile(
    r'^(Lesson|Meaning|Business|Social/Political|Social|Political|Legal|Economics?|'
    r'Healthcare|Technology|Tech|Sport|Education|Finance|Everyday)\s*:\s*', re.I)

# Lines to drop entirely (Czech, page furniture, section banners).
DROP = [re.compile(p, re.I) for p in (
    r'^\s*CZ\s*:',  r'Page\s*\d+\s*$', r'^Group\s+\d+',
    r'^Phrases, Phrasal Verbs', r'^Vocabulary Study Guide', r'^Business English',
    r'^Source:', r'^\s*•', r'^Noun\s+Adjective',
)]

# Everything from here on is reference, not practice -> stop processing.
STOP = re.compile(r'^(Word Families|Study Tips)\b', re.I)

# A headword line e.g. "autocratic (adj.)" or "forge ahead (phr. verb)".
POS = r'(adj|adv|n|v|phr\.?\s*verb|phrase|idiom)'
HEADWORD = re.compile(rf"^[A-Za-z][\w\-' ]*(\([^)]*\)\s*)*\(\s*{POS}[^)]*\)\s*$", re.I)
POS_ONLY = re.compile(rf'^\(\s*{POS}[^)]*\)\s*$', re.I)  # a lone "(adj.)" on its own line


def extract_units(pdf_path, keep_headwords=True, keep_meaning=True):
    """Return a list of clean English practice strings from the PDF."""
    raw = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for ln in (page.extract_text() or "").split("\n"):
                ln = ln.strip()
                if ln:
                    raw.append(ln)

    def is_new_block(s):
        return bool(LABEL.match(s) or HEADWORD.match(s)
                    or any(d.search(s) for d in DROP) or STOP.match(s))

    def is_banner(s):
        return bool(s) and (any(d.search(s) for d in DROP) or bool(STOP.match(s)))

    # --- reflow lines that the PDF wrapped or split ---
    merged, buf = [], ""
    for ln in raw:
        if POS_ONLY.match(ln) and buf and not is_banner(buf):   # lone "(adj.)" -> glue to headword
            buf += " " + ln
        elif buf and not is_banner(buf) and not is_new_block(ln) and not re.search(r'[.!?:]$', buf):
            buf += " " + ln                            # sentence wrapped across lines
        else:
            if buf:
                merged.append(buf)
            buf = ln
    if buf:
        merged.append(buf)

    # --- classify & clean ---
    units = []
    for ln in merged:
        if STOP.match(ln):
            break
        if any(d.search(ln) for d in DROP):
            continue
        m = LABEL.match(ln)
        if m:
            if m.group(1).lower() == "meaning" and not keep_meaning:
                continue
            sent = ln[m.end():].strip()
            if sent:
                units.append(sent)
            continue
        if HEADWORD.match(ln):
            if keep_headwords:
                units.append(re.sub(rf'\s*\(\s*{POS}[^)]*\)\s*$', '', ln, flags=re.I).strip())
            continue
        if re.search(r'[.!?]$', ln):                   # stray sentence that survived reflow
            units.append(ln)
    return units


def load_units_from_txt(txt_path):
    """Read pre-cleaned units from a text file. One unit per line; blank lines and # comments ignored."""
    units = []
    with open(txt_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                units.append(line)
    return units


def to_words(unit):
    """Split a sentence into spoken word tokens (keeps the trailing punctuation)."""
    return [w for w in re.findall(r"[\w''\-]+[.,;:!?]?", unit) if w]


# ----------------------------------------------------------------------------- #
# SYNTHESIS
# ----------------------------------------------------------------------------- #
def _cache_path(text, voice_id, model_id, speed):
    key = hashlib.sha1(f"{model_id}|{voice_id}|{speed}|{text}".encode()).hexdigest()
    return CACHE_DIR / f"{key}.mp3"


def synth(text, voice_id, api_key, model_id, speed=1.0, retries=3):
    """Return MP3 bytes for `text`, using an on-disk cache to avoid re-billing."""
    cp = _cache_path(text, voice_id, model_id, speed)
    if cp.exists():
        return cp.read_bytes()
    url = API_URL.format(voice_id=voice_id)
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed},
    }
    for attempt in range(retries):
        r = requests.post(url, headers={"xi-api-key": api_key,
                                        "Content-Type": "application/json"},
                          json=body, timeout=120)
        if r.status_code == 200:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cp.write_bytes(r.content)
            return r.content
        if r.status_code == 429:                       # rate limited -> back off
            time.sleep(2 * (attempt + 1))
            continue
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    raise RuntimeError("Rate-limited repeatedly; try again later.")


def build_stitch(units, args, api_key):
    """One API call per unit/word, glued with real silence. Supports voice rotation."""
    from pydub import AudioSegment

    out = AudioSegment.silent(duration=400)
    vi = 0  # voice index for rotation

    def speak(text):
        nonlocal vi
        voice = VOICES[vi % len(VOICES)] if args.rotate_voices else VOICES[0]
        vi += 1
        return AudioSegment.from_file(io.BytesIO(synth(text, voice, api_key, args.model, args.speed)),
                                      format="mp3")

    total = len(units)
    for i, unit in enumerate(units, 1):
        print(f"  [{i}/{total}] {unit[:60]}")
        if args.granularity == "word":
            for w in to_words(unit):
                out += speak(w) + AudioSegment.silent(duration=args.pause_words)
            out += AudioSegment.silent(duration=args.pause_unit - args.pause_words)
        else:
            seg = speak(unit)
            for _ in range(args.repeat):
                out += seg + AudioSegment.silent(duration=args.pause_unit)
    return out


def build_breaks(units, args, api_key):
    """Single API call for everything, using <break> tags (cheapest, one voice)."""
    from pydub import AudioSegment

    sec = min(args.pause_unit / 1000, 3.0)             # break tags cap at 3 s
    wsec = min(args.pause_words / 1000, 3.0)
    parts = []
    for unit in units:
        if args.granularity == "word":
            spoken = f' <break time="{wsec}s"/> '.join(to_words(unit))
        else:
            spoken = (unit + f' <break time="{sec}s"/> ') * args.repeat
        parts.append(spoken)
    script = f' <break time="{sec}s"/> '.join(parts)
    audio = synth(script, VOICES[0], api_key, args.model, args.speed)
    return AudioSegment.from_file(io.BytesIO(audio), format="mp3")


# ----------------------------------------------------------------------------- #
# CLI
# ----------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description="PDF vocabulary -> ElevenLabs practice audio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Two-step workflow:\n"
            "  python pdf2practice.py file.pdf --save-txt file.txt   # extract & review\n"
            "  python pdf2practice.py file.txt --rotate-voices -o out.mp3  # synthesise"
        ),
    )
    ap.add_argument("input", help="input PDF or TXT path")
    ap.add_argument("-o", "--out", default="practice.mp3", help="output MP3 path")
    ap.add_argument("--save-txt", metavar="FILE",
                    help="extract PDF units to a text file and exit (one line per unit)")
    ap.add_argument("--mode", choices=["stitch", "breaks"], default="stitch")
    ap.add_argument("--granularity", choices=["sentence", "word"], default="sentence")
    ap.add_argument("--model", default=MODEL_ID, help="ElevenLabs model_id")
    ap.add_argument("--repeat", type=int, default=DEFAULT_REPEAT,
                    help="say each sentence N times")
    ap.add_argument("--pause-unit", type=int, default=DEFAULT_PAUSE_AFTER_UNIT_MS,
                    help="ms of silence after each sentence")
    ap.add_argument("--pause-words", type=int, default=DEFAULT_PAUSE_BETWEEN_WORDS_MS,
                    help="ms of silence between words (word granularity)")
    ap.add_argument("--rotate-voices", action="store_true",
                    help="cycle through VOICES per unit/word")
    ap.add_argument("--voice", metavar="VOICE_ID", action="append", dest="voices",
                    help="ElevenLabs voice ID to use; repeat to build a custom rotation pool")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="speech rate: <1.0 slower, >1.0 faster (default 1.0)")
    ap.add_argument("--no-headwords", action="store_true", help="skip headword cue lines")
    ap.add_argument("--no-meaning", action="store_true", help="skip 'Meaning' definitions")
    ap.add_argument("--dry-run", action="store_true",
                    help="print units and exit (no API calls, no key needed)")
    args = ap.parse_args()

    if args.voices:
        global VOICES
        VOICES = args.voices

    # --- load units ---
    input_path = Path(args.input)
    if input_path.suffix.lower() == ".txt":
        units = load_units_from_txt(input_path)
        print(f"{len(units)} units loaded from {input_path}.\n")
    else:
        units = extract_units(input_path,
                              keep_headwords=not args.no_headwords,
                              keep_meaning=not args.no_meaning)
        print(f"{len(units)} practice units extracted from PDF.\n")

    if not units:
        sys.exit("No practice units found — check the input file.")

    # --- save-txt mode: write text file and exit ---
    if args.save_txt:
        out_txt = Path(args.save_txt)
        out_txt.write_text("\n".join(units), encoding="utf-8")
        print(f"✓ saved {len(units)} units to {out_txt}")
        print(f"  Edit the file, then run:")
        print(f"  python pdf2practice.py {out_txt} [audio flags] -o out.mp3")
        return

    # --- dry-run: print and exit ---
    if args.dry_run:
        for u in units:
            print(" •", u)
        chars = sum(len(u) for u in units)
        print(f"\n~{chars} characters  (≈ {chars} ElevenLabs credits in breaks mode;"
              f" stitch/word uses the same characters but many more requests).")
        return

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit("Set ELEVENLABS_API_KEY (export ELEVENLABS_API_KEY=sk_...).")

    builder = build_breaks if args.mode == "breaks" else build_stitch
    audio = builder(units, args, api_key)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    audio.export(args.out, format="mp3")
    print(f"\n✓ wrote {args.out}  ({len(audio)/1000:.1f} s)")


if __name__ == "__main__":
    main()
