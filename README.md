# render_episode.py — 2D Sprite-Based Episode Renderer

## 1. What this project does

`render_episode.py` is a video rendering engine for 2D **sprite-based** animated characters (visual-novel / cartoon style), fully driven by JSON configuration files.

The script:

1. Loads an **episode** (`episode-settings.json`) describing the scene: background, characters present, their positions, movements, emotions, gaze, and dialogue (visemes).
2. Loads, for each character, a **character-settings.json** file describing its assets (idle/transition/move sprites), eye layers, mouth, blinking, and the anchoring of all these elements.
3. Renders the scene frame by frame (background + characters + eyes + mouth), handling:
   - a **state machine** per character (`idle → transition_out → move → transition_in → idle`),
   - **eye blinking** (FULL / PUPILS / eyelid layer sequence),
   - **gaze** of the pupils, either static or animated via keyframes,
   - **horizontal flip** per animation phase,
   - **position changes** for a character (e.g. a bird hopping from one perch to another),
   - **lip sync** (visemes), if a visemes file is supplied.
4. Assembles all frames into an `.mp4` video via **FFmpeg**.

## 2. How to run it

### Requirements

- Python 3 with `Pillow` (`pip install pillow`)
- `ffmpeg` installed and available on the PATH

### Basic command

```bash
python render_episode.py --settings episodes/episodes-settings/episode-test-spider-settings.json
```

### Available options

| Option | Required | Description |
|---|---|---|
| `--settings` | yes | Path to the episode's JSON file. Relative to the project root or absolute. |
| `--visemes` | no | Path to a JSON visemes file (lip sync, keyed by `speaker`). See section 6. |
| `--output` | no | Name or path of the output video file. Overrides the episode's `output.filename`. If relative, resolved from `episodes/videos/`. If absolute, used as-is. |
| `--keep-frames` | no | Keeps the generated PNG frames in `episodes/videos/frames_debug/` (useful for debugging compositing issues). |

### Examples

```bash
# Simple render
python render_episode.py --settings episodes/episodes-settings/episode-test-spider-settings.json

# With lip sync
python render_episode.py --settings episodes/episodes-settings/ep001.json --visemes episodes/visemes-timeline/episode-001.json

# Custom output + keep frames for debugging
python render_episode.py --settings episodes/episodes-settings/ep001.json --output test_v3.mp4 --keep-frames
```

## 3. Expected folder structure

```
characters/
  <character_id>/
    character-settings.json
    positions/
      <N>/
        idles/
          eyes/<EMOTION>/{FULL,PUPILS,UPPER-EYELID,LOWER-EYELID}.png
          mouths/<EMOTION>/<viseme>.png
        transitions/
          TRANSITION-1.png, TRANSITION-2.png, ...
          eyes/<EMOTION>/...
          mouths/<EMOTION>/...
        moves/
          MOVE-1.png, MOVE-2.png, ...
          eyes/<EMOTION>/...
          mouths/<EMOTION>/...

episodes/
  images/             # backgrounds
  episodes-settings/  # episode JSON files
  visemes-timeline/   # visemes JSON files (lip sync)
  videos/             # output .mp4 (default location)
```

The coordinate system used everywhere is **Figma's**: `(x, y)` = top-left corner.

## 4. Configuring `episode-settings.json`

This file describes **one video**.

```json
{
  "episode_id": "ep001",
  "output": {
    "filename": "episode_001.mp4",
    "duration_seconds": 10,
    "fps": 24,
    "resolution": [1920, 1080]
  },
  "background": { "image": "episode-test-1.png" },
  "characters": [ { ... } ]
}
```

### `characters[]` block

Each entry describes a character present in the scene:

| Field | Description |
|---|---|
| `character` | Folder name under `characters/` |
| `position` | Starting position (key of the `positions` block in character-settings) |
| `screen_position` | `{x, y}` initial on-screen position |
| `scale` | Global scale of the character |
| `overlay_scale` | Additional scale applied to eye/mouth/pupil layers |
| `flip_x` | `bool` **or** dict `{idle_before, move, idle_after}` — horizontal flip per phase |
| `emotions` | `{eyes, mouth}` — emotion subfolder used to pick assets |
| `speaker` | Key matching an entry in the `--visemes` file (optional, for lip sync — see section 6) |
| `gaze` | `{x, y}` default static gaze (x>0 = looking right, y>0 = looking down) |
| `gaze_timeline` | List of keyframes `{at_second, x, y}`, linearly interpolated — overrides `gaze` |
| `gaze_invert_x` / `gaze_invert_y` | Forces gaze inversion for the whole episode, takes priority over character-settings |
| `moves` | List of the character's movements (see below) |
| `dialogue` | Reserved, currently not processed by the renderer |

### `moves[]` block

Each `move` triggers a movement at a given time (`at_second`), made of one or more **segments**.

```json
{
  "at_second": 2.0,
  "transition_fps": 8,
  "move_fps": 10,
  "segments": [
    {
      "position": 2,
      "from": { "x": 600, "y": -235 },
      "to":   { "x": 900, "y": -235 },
      "duration_seconds": 2.0,
      "flip_x": true,
      "gaze_invert_x": true,
      "gaze_invert_y": false,
      "reverse": false,
      "skip_transition": false,
      "gaze": { "x": 5, "y": 0 }
    }
  ]
}
```

Each move segment:

| Field | Description |
|---|---|
| `position` | Position (assets) active during this segment |
| `from` / `to` | Screen coordinates of the start/end, interpolated (lerp) during the move |
| `duration_seconds` | Duration of the move. If omitted, the "natural" duration is computed from the number of `moves` frames × `move_fps` for this position |
| `flip_x` | Full override of the flip for this segment (takes priority over the character's `flip_x`) |
| `gaze_invert_x` / `gaze_invert_y` | Override of gaze inversion for this segment |
| `gaze` | Fixed gaze specific to this segment — if set, used as-is, **without** any automatic inversion |
| `reverse` | Plays the transition/move sequences backwards (useful to visually retrace a path) |
| `skip_transition` | Skips the transition_out/transition_in for this segment (direct chaining) |

A `move` can chain several segments (e.g. idle position 1 → move to position 2 → back to position 1), each with its own settings. The last segment determines the character's final position and idle state until the end of the episode (or until the next move).

**Legacy syntax**, still supported when `segments` is absent:

```json
{
  "at_second": 2.0,
  "from_position": { "x": 600, "y": 150 },
  "to_position": { "x": 900, "y": 150 },
  "move_duration_seconds": 2.0,
  "flip_x": true,
  "to_position_id": 2
}
```

## 5. Configuring `character-settings.json`

Describes the **assets and calibration** of a character, independently of any episode.

### Overall structure

```json
{
  "character_id": "spider",
  "positions": {
    "1": {
      "idle":        { "frames": [...], "fps": 3, "flip_x": false, "default": {...}, "frames_config": [...] },
      "transitions": { "frames": [...], "fps": 3, "flip_x": false, "default": {...}, "frames_config": [...] },
      "moves":       { "frames": [...], "fps": 3, "flip_x": false, "default": {...}, "frames_config": [...] },
      "blink":       { "interval_seconds": 3.5, "interval_jitter_seconds": 1.5,
                        "sequence": ["OPEN.png","HALF-OPEN.png","CLOSED.png","HALF-OPEN.png","OPEN.png"],
                        "frame_duration_seconds": 0.05 },
      "gaze_invert_x": false,
      "gaze_invert_y": false
    }
  }
}
```

A **position** (`"1"`, `"2"`, ...) groups all the assets and settings needed when the character is at a given spot / base pose (e.g. on a low perch vs a high perch). A character can switch position via a `move`.

### `idle` / `transitions` / `moves`

| Field | Description |
|---|---|
| `frames` | Ordered list of base sprite files for this state |
| `fps` | Playback speed of the sequence |
| `flip_x` | Flip applied to this state (can differ between idle/transitions/moves for asset reasons — see below) |
| `default.eye_layers` | `anchor {x,y}`, `scale`, `flip_x`, `rotation` — applied to FULL/UPPER-EYELID/LOWER-EYELID |
| `default.pupils` | `anchor {x,y}` (relative to `eye_layers.anchor`), `offset {x,y}`, `scale`, `flip_x` (optional, otherwise follows `eye_layers`) |
| `default.mouth` | `anchor {x,y}`, `scale`, `flip_x`, `rotation`, `idle_viseme` (only for `idle`) |
| `frames_config` | List of **per base-sprite** overrides (identified by filename via `frame`). Only present keys override the `default` (deep merge for `anchor`/`offset`) |

`idle.frames` is played as a **ping-pong** loop (back and forth). `moves.frames` is played as a simple loop for the duration of the move.

### `blink`

Blinking shared across the whole position, triggered randomly:

- `interval_seconds` / `interval_jitter_seconds`: average interval between two blinks, ± random jitter
- `sequence`: files played in order during a blink (typically OPEN → HALF-OPEN → CLOSED → HALF-OPEN → OPEN)
- `frame_duration_seconds`: duration of each frame of the sequence

### `gaze_invert_x` / `gaze_invert_y` (at position level)

Determines whether the gaze offset should be inverted horizontally/vertically for this position. If absent, `gaze_invert_x` is automatically derived from the `flip_x` of the position's `idle` state (to stay visually consistent regardless of which way the character is drawn).

**Priority order for gaze inversion** (strongest to weakest):
1. `gaze` set directly on a move segment → used as-is, no inversion
2. `gaze_invert_x/y` set on the active move segment
3. `gaze_invert_x/y` set at the character level in the episode
4. `gaze_invert_x/y` set in `character-settings.json` (position level)
5. Automatic deduction from the position's `idle.flip_x`

## 6. Visemes file (lip sync)

If `--visemes` is passed, the renderer reads a JSON file mapping each **speaker id** to a timeline of visemes:

```json
{
  "SPEAKER_01": [
    { "start": 4.000, "end": 4.160, "viseme": "CLOSED" },
    { "start": 4.160, "end": 4.320, "viseme": "A" },
    ...
  ]
}
```

- `start` / `end` are in seconds, relative to the episode timeline.
- `viseme` must match a mouth asset filename (without extension) available for the character's current emotion, e.g. `A.png`, `E.png`, `O.png`, `CLOSED.png`.
- In the episode JSON, a character's `speaker` field must match one of the keys in this file (e.g. `"speaker": "SPEAKER_01"`). If it doesn't match, or if `--visemes` isn't passed, the character's mouth stays on its `idle_viseme`.

### Generating the visemes file

These files were generated using **WhisperX**, which transcribes and time-aligns speech (with speaker diarization), then post-processed into the `{start, end, viseme}` format expected by the renderer. Example command:

```bash
docker run --runtime=nvidia --gpus all -it \
  -v $(pwd):/app \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  whisperx-gpu \
  whisperx /app/episode-test-2-characters.mp3 \
    --model medium \
    --device cuda \
    --diarize \
    --output_dir /app/output \
    --output_format json \
    --language fr
```

This is just the transcription/diarization step — WhisperX's raw output (words + timestamps + speaker labels) still needs to be converted into viseme segments before being usable here. For details on WhisperX itself (models, options, installation), see the project's site: https://github.com/m-bain/whisperX

## 7. Key concepts to remember

- **Scaling hierarchy**: `final_scale = char_scale × overlay_scale × scale(eye_layers|mouth|pupils)`
- **Eye layers**: `FULL` (eye white, always present) → `PUPILS` (positioned separately) → `UPPER-EYELID` → `LOWER-EYELID`, stacked according to the blink state (OPEN / HALF-OPEN / CLOSED)
- **Flip**: each flip (`base`, `eye`, `mouth`, `pupils`) results from a **XOR** between the character/segment's global flip and the element's own flip — this lets a layer stay "upright" even when the whole character is mirrored
- **`_positioned`**: internal marker preventing the default idle fill from overwriting a position already computed by a move
- Pupil x/y positions are computed **once, as integers** (rounded), then reused everywhere to avoid pixel jitter from repeated rounding

## 8. Minimal end-to-end example

1. Prepare `characters/bird/character-settings.json` with at least a position `"1"`.
2. Prepare the assets under `characters/bird/positions/1/idles/...`, `transitions/...`, `moves/...`.
3. Create an episode:

```json
{
  "episode_id": "test",
  "output": { "filename": "test.mp4", "duration_seconds": 5, "fps": 24, "resolution": [1920, 1080] },
  "background": { "image": "background.png" },
  "characters": [
    {
      "character": "bird",
      "position": 1,
      "screen_position": { "x": 400, "y": 200 },
      "scale": 0.5,
      "emotions": { "eyes": "HAPPY", "mouth": "HAPPY" }
    }
  ]
}
```

4. Run:

```bash
python render_episode.py --settings episodes/episodes-settings/test.json
```
