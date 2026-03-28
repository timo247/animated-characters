#!/usr/bin/env python3

import os
import json
import shutil
import subprocess
import argparse
import random
from PIL import Image

FPS = 25


# ======================
# UTILS
# ======================

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def get_total_duration_multi(timelines):
    max_end = 0
    for timeline in timelines.values():
        for seg in timeline:
            max_end = max(max_end, seg["end"])
    return max_end


def time_to_frame(t):
    return int(t * FPS)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


# ======================
# IMAGE LOADERS
# ======================

def load_mouth_image(viseme, base_path, emotion):
    path = os.path.join(base_path, "mouths", emotion, f"{viseme}.png")
    if not os.path.exists(path):
        raise ValueError(f"Viseme image not found: {path}")
    return Image.open(path).convert("RGBA")


def load_eye_image(state, base_path, emotion):

    eyes_root = os.path.join(base_path, "eyes")

    emotion_path = os.path.join(eyes_root, emotion, f"{state}.png")
    if os.path.isfile(emotion_path):
        return Image.open(emotion_path).convert("RGBA")

    neutral_path = os.path.join(eyes_root, f"{state}.png")
    if os.path.isfile(neutral_path):
        return Image.open(neutral_path).convert("RGBA")

    raise ValueError(f"Eye image not found: {emotion_path} / {neutral_path}")


# ======================
# EYE TIMELINE
# ======================

def generate_eye_timeline(duration, cfg):

    timeline = []
    current_time = 0.0

    while current_time < duration:

        interval = random.uniform(cfg.get("min_blink_interval", 3.0),
                                  cfg.get("max_blink_interval", 5.5))

        blink_d = random.uniform(cfg.get("min_blink_duration", 0.18),
                                cfg.get("max_blink_duration", 0.28))

        start = current_time + interval
        end = start + blink_d

        if start >= duration:
            timeline.append({"start": current_time, "end": duration, "eye": "OPEN"})
            break

        timeline.append({"start": current_time, "end": start, "eye": "OPEN"})

        half = blink_d * 0.3
        closed = blink_d * 0.4

        timeline.append({"start": start, "end": start + half, "eye": "HALF-OPEN"})
        timeline.append({"start": start + half, "end": start + half + closed, "eye": "CLOSED"})
        timeline.append({"start": start + half + closed, "end": end, "eye": "HALF-OPEN"})

        current_time = end

    return timeline


# ======================
# LOOKUP HELPERS
# ======================

def get_current_viseme(timeline, t):
    for seg in timeline:
        if seg["start"] <= t < seg["end"]:
            return seg["viseme"]
    return "CLOSED"


def get_current_eye(timeline, t):
    for seg in timeline:
        if seg["start"] <= t < seg["end"]:
            return seg["eye"]
    return "OPEN"


# ======================
# MAIN RENDER
# ======================

def render(episode):

    BASE_IMAGE_PATH = f"episodes/images/{episode}.png"
    TIMELINE_PATH = f"episodes/visemes-timeline/{episode}.json"
    POSITION_PATH = f"episodes/positions-mapping/{episode}.json"
    AUDIO_PATH = f"episodes/audios/{episode}.mp3"

    OUTPUT_DIR = "episodes/videos"
    OUTPUT_VIDEO = f"{OUTPUT_DIR}/{episode}.mp4"
    FRAMES_DIR = f"tmp_frames_{episode}"

    print("=================================")
    print(f"Rendering episode : {episode}")
    print("=================================")

    timelines = load_json(TIMELINE_PATH)  # dict speaker -> timeline
    config = load_json(POSITION_PATH)

    characters = config["characters"]

    base_image = Image.open(BASE_IMAGE_PATH).convert("RGBA")

    total_duration = get_total_duration_multi(timelines)
    total_frames = time_to_frame(total_duration)

    ensure_dir(FRAMES_DIR)
    ensure_dir(OUTPUT_DIR)

    # caches
    mouth_cache = {}
    eye_cache = {}

    # eye timelines par personnage
    eye_timelines = {}

    for char in characters:
        eye_timelines[char["name"]] = generate_eye_timeline(
            total_duration,
            char.get("eyes", {})
        )

    # ======================
    # FRAME LOOP
    # ======================

    for frame_number in range(total_frames):

        current_time = frame_number / FPS
        frame = base_image.copy()

        for char in characters:

            name = char["name"]
            speaker = char["speaker"]
            position = char["position"]

            timeline = timelines.get(speaker, [])

            current_viseme = get_current_viseme(timeline, current_time)

            # emotion
            emotion_1 = char.get("emotion_1", "HAPPY")
            emotion_2 = char.get("emotion_2", emotion_1)
            transition = char.get("emotion_transition_time", float("inf"))

            current_emotion = emotion_1 if current_time < transition else emotion_2

            base_path = f"characters/{name}/positions/{position}"

            # ======================
            # MOUTH
            # ======================

            mouth_key = f"{name}_{position}_{current_emotion}_{current_viseme}"

            if mouth_key not in mouth_cache:
                mouth_cache[mouth_key] = load_mouth_image(
                    current_viseme,
                    base_path,
                    current_emotion
                )

            mouth_img = mouth_cache[mouth_key]
            m_cfg = char["mouth"]

            m = mouth_img.copy()

            m = m.resize(
                (int(m.width * m_cfg.get("scale", 1)),
                 int(m.height * m_cfg.get("scale", 1))),
                Image.LANCZOS
            )

            if m_cfg.get("flip_x", False):
                m = m.transpose(Image.FLIP_LEFT_RIGHT)

            m = m.rotate(m_cfg.get("rotation", 0), expand=True)

            # ======================
            # EYES
            # ======================

            eye_state = get_current_eye(
                eye_timelines[name],
                current_time
            )

            eye_key = f"{name}_{position}_{current_emotion}_{eye_state}"

            if eye_key not in eye_cache:
                eye_cache[eye_key] = load_eye_image(
                    eye_state,
                    base_path,
                    current_emotion
                )

            eye_img = eye_cache[eye_key]
            e_cfg = char["eyes"]

            e = eye_img.copy()

            e = e.resize(
                (int(e.width * e_cfg.get("scale", 1)),
                 int(e.height * e_cfg.get("scale", 1))),
                Image.LANCZOS
            )

            if e_cfg.get("flip_x", False):
                e = e.transpose(Image.FLIP_LEFT_RIGHT)

            e = e.rotate(e_cfg.get("rotation", 0), expand=True)

            # ======================
            # COMPOSITING
            # ======================

            frame.paste(e, (int(e_cfg["x"]), int(e_cfg["y"])), e)
            frame.paste(m, (int(m_cfg["x"]), int(m_cfg["y"])), m)

        # save frame
        frame_path = os.path.join(FRAMES_DIR, f"frame_{frame_number:05d}.png")
        frame.save(frame_path)

        if frame_number % 50 == 0:
            print(f"Frame {frame_number}/{total_frames}")

    # ======================
    # FFMPEG
    # ======================

    print("Encoding video...")

    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", f"{FRAMES_DIR}/frame_%05d.png",
        "-i", AUDIO_PATH,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        OUTPUT_VIDEO
    ], check=True)

    print("Video generated:", OUTPUT_VIDEO)

    shutil.rmtree(FRAMES_DIR)


# ======================

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", required=True)

    args = parser.parse_args()

    render(args.episode)
