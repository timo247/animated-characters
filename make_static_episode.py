#!/usr/bin/env python3
"""
make_static_episode.py
----------------------
Genere un episode-settings.json "template" qui ne contient que des statics
joues l'un apres l'autre, a partir de statics/<static>/static-settings.json.

Usage :
    python make_static_episode.py kung-fu-man \\
        "rotating-knees:3, squatting:2, standing:1.5s, rotating-hips" \\
        --x 110 --y -161

Sequence (separee par des virgules, dans l'ordre de lecture) :
    idle        -> 1 loop
    idle:N      -> N loops complets (N entier)
    idle:Ts     -> T secondes (ex: 1.5s), l'idle est coupe net a la fin

Un "loop" = un cycle complet de l'idle, pause_seconds comprise (le renderer
fait d'abord la pause sur la 1ere frame, puis joue le cycle). Les durees sont
calculees comme le renderer le fait (fps / duration_seconds / hold), en frames
video entieres : chaque static demarre pile a la fin du precedent.

Options utiles : --color (defaut : 1ere couleur de l'idle), --id (id de
l'episode), --background, --fps, --resolution W H, --scale, --flip-x, --out.
Si l'episode dure plus que la somme des statics (duree arrondie a la seconde
superieure), la fin de l'episode reste vide : ajuste "duration_seconds".
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()


def num(v):
    """12.0 -> 12 (JSON plus propre, comme dans les episode-settings existants)."""
    v = round(v, 6)
    return int(v) if v == int(v) else v


def die(msg):
    sys.exit(f"[ERREUR] {msg}")


def parse_sequence(text):
    """-> liste de (idle, 'loops'|'seconds', valeur)"""
    items = []
    for raw in text.split(","):
        raw = raw.strip()
        if not raw:
            continue
        name, sep, amount = raw.partition(":")
        name, amount = name.strip(), amount.strip()
        if not name:
            die(f"Element de sequence invalide : '{raw}'")
        if not sep:
            items.append((name, "loops", 1))
        elif re.fullmatch(r"\d+", amount):
            if int(amount) < 1:
                die(f"'{raw}' : le nombre de loops doit etre >= 1")
            items.append((name, "loops", int(amount)))
        elif re.fullmatch(r"\d+(\.\d+)?s", amount):
            if float(amount[:-1]) <= 0:
                die(f"'{raw}' : la duree doit etre > 0")
            items.append((name, "seconds", float(amount[:-1])))
        else:
            die(f"'{raw}' : attendu 'idle', 'idle:N' (loops) ou 'idle:Ts' (secondes)")
    if not items:
        die("Sequence vide.")
    return items


def idle_period_frames(idle_name, idle_cfg, color, fps):
    """Duree (frames video) d'un loop : pause + cycle, comme le renderer."""
    frames = idle_cfg["colors"][color]["frames"]
    if "duration_seconds" in idle_cfg:
        fps_cfg = len(frames) / float(idle_cfg["duration_seconds"])
    else:
        fps_cfg = idle_cfg.get("fps", 8)
    hold = max(1, round(fps / fps_cfg))
    cycle = len(frames) * hold

    pause_f = 0
    pause = idle_cfg.get("pause_seconds")
    if pause:
        p_min = float(pause.get("min", 3.0))
        p_max = float(pause.get("max", p_min))
        if p_min != p_max:
            print(f"  [WARN] idle '{idle_name}' : pause aleatoire ({p_min}-{p_max}s) "
                  f"-> duree calculee avec min ; la coupe finale sera approximative.")
        pause_f = int(p_min * fps + 1e-9)
    return pause_f + cycle


def main():
    ap = argparse.ArgumentParser(description="Genere un episode-settings a partir d'une liste d'idles.")
    ap.add_argument("static", help="id du static (dossier statics/<id>/)")
    ap.add_argument("sequence", help='ex: "rotating-knees:3, squatting:2, standing:1.5s"')
    ap.add_argument("--x", type=float, required=True)
    ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--color", default=None)
    ap.add_argument("--id", dest="episode_id", default=None)
    ap.add_argument("--background", default="")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--resolution", type=int, nargs=2, default=[1024, 1024], metavar=("W", "H"))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--flip-x", action="store_true")
    ap.add_argument("--static-settings", default=None, help="chemin explicite du static-settings.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    settings_path = Path(a.static_settings) if a.static_settings else \
        PROJECT_ROOT / "statics" / a.static / "static-settings.json"
    if not settings_path.exists():
        die(f"static-settings.json introuvable : {settings_path}")
    idles = json.loads(settings_path.read_text(encoding="utf-8")).get("idles", {})

    seq = parse_sequence(a.sequence)
    for name, _, _ in seq:
        if name not in idles:
            die(f"Idle '{name}' inconnu. Disponibles : {', '.join(idles)}")

    color = a.color or next(iter(idles[seq[0][0]]["colors"]))
    for name, _, _ in seq:
        if color not in idles[name].get("colors", {}):
            die(f"Couleur '{color}' absente de l'idle '{name}'.")

    episode_id = a.episode_id or f"{a.static}-routine"
    pos = {"x": num(a.x), "y": num(a.y)}

    statics, cursor = [], 0
    print(f"  {'static':<42}{'debut':>8}{'duree':>8}")
    for i, (name, kind, value) in enumerate(seq, 1):
        if kind == "loops":
            dur_f = value * idle_period_frames(name, idles[name], color, a.fps)
        else:
            dur_f = max(1, round(value * a.fps))
        statics.append({
            "id": f"{a.static}-{i}",
            "static": a.static,
            "color": color,
            "layer": 0,
            "idle": name,
            "screen_position": dict(pos),
            "scale": num(a.scale),
            "flip_x": a.flip_x,
            "active_window": {
                "start_second": num(cursor / a.fps),
                "duration_seconds": num(dur_f / a.fps),
            },
        })
        label = f"{name} ({value if kind == 'loops' else str(value) + 's'}{' loops' if kind == 'loops' else ''})"
        print(f"  {label:<42}{cursor / a.fps:>7.2f}s{dur_f / a.fps:>7.2f}s")
        cursor += dur_f

    total_s = cursor / a.fps
    duration = max(1, math.ceil(total_s - 1e-9))
    w, h = a.resolution
    episode = {
        "episode_id": episode_id,
        "output": {
            "filename": f"{episode_id}.mp4",
            "duration_seconds": duration,
            "fps": a.fps,
            "resolution": [w, h],
        },
        "background": {"image": a.background},
        "audio": "",
        "visemes": "",
        "camera": {
            "zoom": 1, "x": num(w / 2), "y": num(h / 2),
            "timeline": [{"at_second": 0, "zoom": 1, "x": num(w / 2), "y": num(h / 2)}],
        },
        "characters": [],
        "decors": [],
        "statics": statics,
    }

    out = Path(a.out) if a.out else \
        PROJECT_ROOT / "episodes" / "episodes-settings" / f"{episode_id}-settings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(episode, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n  Total {total_s:.2f}s -> duration_seconds = {duration}")
    print(f"[OK] {out}")


if __name__ == "__main__":
    main()
