#!/usr/bin/env python3

import re
import json
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple

# --------------------------------------------------
# CONFIG ANIMATION
# --------------------------------------------------

VISEME_OFFSET = -0.04   # anticipation visuelle (40ms)
MICRO_PAUSE = 0.03      # durée fermeture fin syllabe
MIN_DURATION = 0.05     # sécurité

# --------------------------------------------------
# DATA STRUCTURE
# --------------------------------------------------

@dataclass
class VisemeEvent:
    start: float
    end: float
    viseme: str


# --------------------------------------------------
# LOAD VISEME RULES
# --------------------------------------------------

def load_viseme_rules(path: str) -> Tuple[List[Tuple[re.Pattern, str]], str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    compiled_rules = [
        (re.compile(rule["pattern"]), rule["viseme"])
        for rule in data["rules"]
    ]

    default_viseme = data.get("default", "CLOSED")

    return compiled_rules, default_viseme


# --------------------------------------------------
# ASS TIME
# --------------------------------------------------

def ass_time_to_seconds(t: str) -> float:
    h, m, s = t.split(":")
    s, cs = s.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


# --------------------------------------------------
# ASS PARSER (karaoke)
# --------------------------------------------------

def parse_ass_karaoke(file_path: str):
    timeline = []

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    dialogue_lines = [l for l in lines if l.startswith("Dialogue:")]

    for line in dialogue_lines:
        parts = line.split(",", 9)
        start_time = ass_time_to_seconds(parts[1])
        text = parts[9]

        pattern = re.findall(r"{\\k(\d+)}([^{}]+)", text)

        current_time = start_time

        for duration_cs, syllable in pattern:
            duration = int(duration_cs) / 100.0

            if duration < MIN_DURATION:
                continue

            timeline.append({
                "start": current_time,
                "end": current_time + duration,
                "text": syllable.strip()
            })

            current_time += duration

    return timeline


# --------------------------------------------------
# TEXT → VISEME (FIRST MATCH WINS)
# --------------------------------------------------

def text_to_viseme(text: str, rules, default_viseme: str) -> str:
    text = text.lower()

    for pattern, viseme in rules:
        if pattern.search(text):
            return viseme

    return default_viseme


# --------------------------------------------------
# MERGE CONSECUTIVE IDENTICAL VISEMES
# --------------------------------------------------

def merge_consecutive_visemes(events: List[VisemeEvent]) -> List[VisemeEvent]:
    if not events:
        return []

    merged = [events[0]]

    for event in events[1:]:
        last = merged[-1]

        if event.viseme == last.viseme:
            last.end = event.end
        else:
            merged.append(event)

    return merged


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Convert ASS karaoke to viseme timeline JSON")
    parser.add_argument("input_ass", help="Input ASS file")
    parser.add_argument("output_json", help="Output JSON file")
    parser.add_argument("viseme_config", help="Viseme JSON config file")

    args = parser.parse_args()

    rules, default_viseme = load_viseme_rules(args.viseme_config)
    raw_segments = parse_ass_karaoke(args.input_ass)

    timeline = []

    for segment in raw_segments:

        base_start = segment["start"]
        base_end = segment["end"]

        # anticipation
        start = max(0, base_start + VISEME_OFFSET)
        end = base_end

        viseme = text_to_viseme(segment["text"], rules, default_viseme)

        total_duration = end - start
        if total_duration <= 0:
            continue

        # fermeture en fin de syllabe
        close_duration = min(MICRO_PAUSE, total_duration * 0.4)
        main_end = end - close_duration

        # viseme principal
        timeline.append(
            VisemeEvent(
                start,
                main_end,
                viseme
            )
        )

        # fermeture systématique
        timeline.append(
            VisemeEvent(
                main_end,
                end,
                "CLOSED"
            )
        )

    timeline = merge_consecutive_visemes(timeline)

    output = [
        {
            "start": round(event.start, 3),
            "end": round(event.end, 3),
            "viseme": event.viseme
        }
        for event in timeline
    ]

    Path(args.output_json).write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"Generated {len(output)} viseme events.")


if __name__ == "__main__":
    main()
