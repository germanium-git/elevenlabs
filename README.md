# pdf2practice — Linguahouse PDF → ElevenLabs practice audio

Turns a vocabulary PDF into a single MP3 of English example sentences with
spaced-out pauses for shadowing/repeating. Czech lines and category labels
(Business:, Legal:, Sport: ...) are stripped automatically.

## One-time setup
```bash
pip install -r requirements.txt
export ELEVENLABS_API_KEY=sk_xxx   # elevenlabs.io -> Profile icon -> API Key
```

## Recommended two-step workflow

```bash
# Step 1 — extract PDF to a text file, then open and edit it:
python pdf2practice.py file.pdf --save-txt file.txt

# (edit file.txt: delete unwanted lines, fix anything the extractor got wrong)

# Step 2 — synthesise audio from the cleaned text:
python pdf2practice.py file.txt --repeat 2 --speed 0.8 --rotate-voices -o practice.mp3
```

The text file is one sentence per line. Blank lines and lines starting with `#` are ignored.

## One-shot workflow

```bash
# 1. See exactly what will be spoken (free, no key needed):
python pdf2practice.py file.pdf --dry-run

# 2. Make the audio (one voice, cheapest, pauses via break tags):
python pdf2practice.py file.pdf --mode breaks -o practice.mp3

# 3. Different voice per sentence + exact pauses (recommended):
python pdf2practice.py file.pdf --mode stitch --rotate-voices -o practice.mp3

# 4. Word-by-word drill, a new voice on every word:
python pdf2practice.py file.pdf --mode stitch --granularity word --rotate-voices
```

## Useful flags
| flag | effect |
|------|--------|
| `--save-txt file.txt` | extract PDF to editable text file and exit |
| `--voice VOICE_ID` | use a specific voice; repeat the flag to build a custom rotation pool |
| `--speed 0.8` | slow speech to 80 % of normal (0.7–1.0 practical range) |
| `--repeat 2` | say each sentence twice (hear it, then echo it) |
| `--pause-unit 2000` | 2 s of silence after each sentence |
| `--pause-words 800` | 0.8 s between words (word granularity) |
| `--no-headwords` | drop the single-word cue lines |
| `--no-meaning` | drop the dictionary-style definitions |
| `--model eleven_flash_v2_5` | cheaper/faster model |

## Notes
- Synthesised audio is cached in `~/.pdf2practice_cache`, so re-running after a
  tweak only pays for the units that changed.
- `breaks` mode = 1 API request total. `stitch` mode = 1 request per sentence
  (or per word) — more requests, but lets the voice change and removes the 3 s
  pause cap. Both bill the same number of characters (1 credit = 1 character).
- To change the default voice pool, edit the `VOICES` list at the top of the script with
  Voice IDs from elevenlabs.io → Voices.
