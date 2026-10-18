"""
Viseme timeline generator — v4 (phonèmes IPA).

Par rapport à la v3 :

  1. Temps
     - décalage global CONSTANT (VISEME_OFFSET) appliqué à toute la timeline,
       au lieu d'avancer seulement le début des mots (qui les étirait et
       créait des événements qui se chevauchent) ;
     - le mute inter-speakers est calculé dans le même repère temporel, donc
       il ne coupe plus la fin de la parole du locuteur précédent ;
     - la fin d'un mot est plafonnée à une durée plausible : les timestamps
       WhisperX étirent souvent le dernier mot avant une pause jusqu'au mot
       suivant, la bouche retombe alors sur CLOSED au lieu de rester ouverte.

  2. Phonétique
     - espeak-ng (via phonemizer, fr-fr) donne l'IPA de chaque mot, puis une
       petite table IPA -> visème (viseme_config_ipa.json) remplace regex et
       syllabes. Le jeu de visèmes est inchangé : CLOSED, O, A, E, F, L, CONS.
     - durées par phonème pondérées (voyelles > consonnes, e muet très court,
       allongement de la dernière voyelle du mot).

  3. Post-traitement
     - fusion des formes trop courtes dans leur voisin (la voyelle l'emporte) ;
     - anticipation d'une frame des bilabiales (p/b/m) et des voyelles arrondies ;
     - timeline continue : les silences >= PAUSE_THRESHOLD deviennent CLOSED.

Dépendances :
    pip install phonemizer
    espeak-ng  (apt install espeak-ng | brew install espeak-ng | installeur
                Windows + variable PHONEMIZER_ESPEAK_LIBRARY)
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# ── Paramètres globaux ───────────────────────────────────────────────────────

VISEME_OFFSET   = -0.08   # s — décalage global de TOUTE la timeline (négatif = la bouche
                          #     bouge plus tôt que l'audio). Surchargeable avec --offset.
PAUSE_THRESHOLD = 0.15    # s — silence entre deux mots à partir duquel la bouche se ferme
MIN_DURATION    = 0.04    # s — fragments plus courts supprimés lors du mute inter-speakers
MIN_SHAPE = {             # s — durée minimale d'une forme, sinon fusionnée dans son voisin
    "default": 0.06,      #     (~2 frames à 30 fps)
    "CLOSED":  0.035,     #     fermeture bilabiale : visuellement importante, seuil plus bas
}
ANTICIPATION     = 0.03   # s — avance des bilabiales et voyelles arrondies sur la forme précédente
MAX_UNIT_DUR     = 0.13   # s — durée max d'une "unité de poids" de phonème ; plafonne la parole
                          #     d'un mot (None = désactivé, le mot occupe tout son intervalle)
FINAL_LENGTHENING = 1.5   # poids de la dernière voyelle d'un mot (allongement final du français)

# Priorité pour absorber une forme trop courte : la voyelle l'emporte sur la consonne
PRIORITY = {"A": 3, "O": 3, "E": 3, "CLOSED": 2, "F": 2, "L": 2, "CONS": 1}

# ── Classes de phonèmes (pour les durées) ────────────────────────────────────

VOWELS  = set("aɑeɛəiɔoœøuyɐɜɪʊ")
GLIDES  = set("jwɥ")
STOPS   = set("pbtdkɡg")
FRICAT  = set("fvszʃʒxh")
NASALC  = set("mnɲŋ")
LIQUIDS = set("lʁrɾ")


# ── Data class ───────────────────────────────────────────────────────────────

@dataclass
class Event:
    start: float
    end: float
    viseme: str
    kind: str = "phone"      # "phone" (issu d'un phonème) ou "pause" (silence / mute)

    @property
    def dur(self) -> float:
        return self.end - self.start


# ── Utilitaires IPA ──────────────────────────────────────────────────────────

def _nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def base_char(ph: str) -> str:
    """Phonème sans diacritiques (nasalisation, longueur…) : 'ɑ̃' -> 'ɑ'."""
    kept = [c for c in _nfd(ph) if unicodedata.category(c) not in ("Mn", "Mk", "Lm", "Sk")]
    return "".join(kept)


def is_full_vowel(ph: str) -> bool:
    b = base_char(ph)
    return b in VOWELS and b != "ə"


def phone_weight(ph: str) -> float:
    b = base_char(ph)
    nasal = "̃" in _nfd(ph)
    if b == "ə":
        return 0.4
    if b in VOWELS:
        return 1.15 if nasal else 1.0
    if b in GLIDES:
        return 0.5
    if b in STOPS:
        return 0.55
    if b in FRICAT:
        return 0.8
    if b in NASALC or b in LIQUIDS:
        return 0.65
    return 0.6


# ── Chargement de la config ──────────────────────────────────────────────────

def load_viseme_config(path: str) -> Tuple[Dict[str, str], str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    mapping: Dict[str, str] = {}
    for viseme, phones in data["visemes"].items():
        for ph in phones:
            mapping[_nfd(ph)] = viseme
    return mapping, data.get("default", "CONS")


UNMAPPED: Counter = Counter()


def viseme_for(ph: str, mapping: Dict[str, str], default: str) -> str:
    key = _nfd(ph)
    if key in mapping:
        return mapping[key]
    b = base_char(ph)
    if b in mapping:
        return mapping[b]
    UNMAPPED[ph] += 1
    return default


# ── Phonémisation (espeak-ng via phonemizer) ─────────────────────────────────

def clean_text(word: str) -> str:
    w = word.lower().replace("’", "'")
    w = re.sub(r"[^\w'-]", "", w)
    return w.strip("'-_")


def phonemize_words(words: Iterable[str]) -> Dict[str, List[str]]:
    """Retourne {mot nettoyé: [phonèmes IPA]} pour tous les mots uniques."""
    try:
        from phonemizer.backend import EspeakBackend
        from phonemizer.separator import Separator
    except ImportError:
        sys.exit("phonemizer est introuvable : pip install phonemizer")

    try:
        backend = EspeakBackend(
            "fr-fr",
            with_stress=False,
            language_switch="remove-flags",
            words_mismatch="ignore",
        )
    except RuntimeError as exc:
        sys.exit(
            f"espeak-ng est introuvable ({exc}).\n"
            "  Linux : apt install espeak-ng   |   macOS : brew install espeak-ng\n"
            "  Windows : installer espeak-ng, puis définir PHONEMIZER_ESPEAK_LIBRARY "
            "vers libespeak-ng.dll"
        )

    unique = sorted({clean_text(w) for w in words} - {""})
    if not unique:
        return {}
    lines = backend.phonemize(unique, separator=Separator(phone=" ", word=" | "), strip=True)
    result: Dict[str, List[str]] = {}
    for w, line in zip(unique, lines):
        phones = [t for t in line.split() if t != "|" and base_char(t)]
        result[w] = phones
    return result


# ── 1/2. Événements par mot ──────────────────────────────────────────────────

def word_events(start: float, end: float, phones: List[str],
                mapping: Dict[str, str], default: str) -> List[Event]:
    """Répartit les phonèmes d'un mot sur [start, end], parole plafonnée à une durée plausible."""
    if not phones:
        return []
    weights = [phone_weight(p) for p in phones]
    for i in range(len(phones) - 1, -1, -1):          # allongement de la dernière voyelle
        if is_full_vowel(phones[i]):
            weights[i] *= FINAL_LENGTHENING
            break
    total = sum(weights)
    dur = end - start
    if MAX_UNIT_DUR:
        dur = min(dur, total * MAX_UNIT_DUR)
    events: List[Event] = []
    t = start
    for ph, w in zip(phones, weights):
        d = dur * w / total
        events.append(Event(t, t + d, viseme_for(ph, mapping, default)))
        t += d
    events[-1].end = start + dur
    return events


# ── 3. Post-traitement ───────────────────────────────────────────────────────

def fill_gaps(events: List[Event]) -> List[Event]:
    """Timeline continue : silence >= PAUSE_THRESHOLD -> CLOSED, sinon la forme précédente se prolonge."""
    out: List[Event] = []
    for ev in events:
        if out:
            gap = ev.start - out[-1].end
            if gap >= PAUSE_THRESHOLD:
                out.append(Event(out[-1].end, ev.start, "CLOSED", "pause"))
            elif gap > 1e-9:
                out[-1].end = ev.start
        out.append(ev)
    if out and out[0].start > 1e-9:
        out.insert(0, Event(0.0, out[0].start, "CLOSED", "pause"))
    return out


def merge_same(events: List[Event], ignore_kind: bool = False) -> List[Event]:
    out: List[Event] = []
    for ev in events:
        if (out and out[-1].viseme == ev.viseme
                and (ignore_kind or out[-1].kind == ev.kind)
                and ev.start - out[-1].end < 1e-6):
            out[-1].end = max(out[-1].end, ev.end)
        else:
            out.append(ev)
    return out


def _min_shape(ev: Event) -> float:
    return MIN_SHAPE.get(ev.viseme, MIN_SHAPE["default"])


def absorb_short(events: List[Event]) -> List[Event]:
    """Fusionne dans leur voisin les formes plus courtes que leur seuil (la plus courte d'abord)."""
    while True:
        short = [i for i, e in enumerate(events) if e.kind == "phone" and e.dur < _min_shape(e)]
        for i in sorted(short, key=lambda k: events[k].dur):
            nbrs = [j for j in (i - 1, i + 1)
                    if 0 <= j < len(events) and events[j].kind == "phone"]
            if not nbrs:
                continue                    # collée à une pause : on la garde
            j = max(nbrs, key=lambda k: (PRIORITY.get(events[k].viseme, 1), events[k].dur))
            if j < i:
                events[j].end = events[i].end
            else:
                events[j].start = events[i].start
            del events[i]
            break
        else:
            return events


def anticipate(events: List[Event]) -> List[Event]:
    """Les bilabiales (CLOSED) et voyelles arrondies (O) arrivent un peu avant leur phonème."""
    for i in range(1, len(events)):
        cur, prev = events[i], events[i - 1]
        if cur.kind == "phone" and prev.kind == "phone" and cur.viseme in ("O", "CLOSED"):
            shift = min(ANTICIPATION, 0.4 * prev.dur)
            cur.start -= shift
            prev.end -= shift
    return events


def build_timeline(words: List[dict], phone_map: Dict[str, List[str]],
                   mapping: Dict[str, str], default: str) -> List[Event]:
    events: List[Event] = []
    prev_end = 0.0
    for w in sorted(words, key=lambda x: x["start"]):
        start = max(w["start"], prev_end)           # pas de chevauchement entre mots
        end = w["end"]
        prev_end = max(prev_end, end)
        if end - start < 0.02:
            continue
        phones = phone_map.get(clean_text(w["word"]), [])
        events.extend(word_events(start, end, phones, mapping, default))

    events = fill_gaps(events)
    events = merge_same(events)
    events = absorb_short(events)
    events = merge_same(events)
    events = anticipate(events)
    return events


def shift_events(events: List[Event], offset: float) -> List[Event]:
    out: List[Event] = []
    for e in events:
        s, t = e.start + offset, e.end + offset
        if t <= 0:
            continue
        out.append(Event(max(0.0, s), t, e.viseme, e.kind))
    return out


# ── Mute inter-speakers ──────────────────────────────────────────────────────

def speaking_intervals(words: List[dict], offset: float) -> List[Tuple[float, float]]:
    """Intervalles de parole fusionnés, dans le même repère temporel que la timeline décalée."""
    raw = [(max(0.0, w["start"] + offset), w["end"] + offset)
           for w in words if w["end"] - w["start"] >= 0.02]
    if not raw:
        return []
    raw.sort()
    merged = [raw[0]]
    for s, e in raw[1:]:
        if s <= merged[-1][1] + PAUSE_THRESHOLD:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def enforce_silence_between_speakers(events: List[Event],
                                     other_intervals: List[Tuple[float, float]]) -> List[Event]:
    """Force CLOSED pendant que les autres speakers parlent (tronque/supprime les événements)."""
    if not other_intervals or not events:
        return events
    for oa, ob in other_intervals:
        new_events: List[Event] = []
        for ev in events:
            if ev.end <= oa or ev.start >= ob:
                new_events.append(ev)
                continue
            if ev.start < oa and oa - ev.start >= MIN_DURATION:
                new_events.append(Event(ev.start, oa, ev.viseme, ev.kind))
            if ev.end > ob and ev.end - ob >= MIN_DURATION:
                new_events.append(Event(ob, ev.end, ev.viseme, ev.kind))
        new_events.append(Event(oa, ob, "CLOSED", "pause"))
        events = new_events
    events.sort(key=lambda e: e.start)
    return events


def to_json(events: List[Event]) -> List[dict]:
    events = sorted(events, key=lambda e: e.start)
    filled: List[Event] = []
    for ev in events:                       # trous (fragments supprimés, silences) -> CLOSED
        if filled and ev.start - filled[-1].end > 1e-3:
            filled.append(Event(filled[-1].end, ev.start, "CLOSED", "pause"))
        filled.append(ev)
    events = merge_same(filled, ignore_kind=True)
    return [
        {"start": round(e.start, 3), "end": round(e.end, 3), "viseme": e.viseme}
        for e in events if e.dur >= 0.001
    ]


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Génère une timeline de visèmes par speaker à partir d'un JSON de transcription."
    )
    parser.add_argument("input_json",    help="Fichier JSON d'entrée (transcription WhisperX)")
    parser.add_argument("output_json",   help="Fichier JSON de sortie (timeline visèmes)")
    parser.add_argument("viseme_config", help="Fichier JSON de mapping IPA -> visèmes")
    parser.add_argument("--offset", type=float, default=VISEME_OFFSET,
                        help=f"décalage global en secondes (défaut {VISEME_OFFSET})")
    args = parser.parse_args()

    mapping, default = load_viseme_config(args.viseme_config)
    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))

    speakers: Dict[str, List[dict]] = {}
    skipped = 0
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            if "start" not in word or "end" not in word:
                skipped += 1                          # mot non aligné par WhisperX
                continue
            spk = word.get("speaker") or segment.get("speaker") or "UNKNOWN"
            speakers.setdefault(spk, []).append(word)

    phone_map = phonemize_words(w["word"] for ws in speakers.values() for w in ws)

    timelines: Dict[str, List[Event]] = {
        spk: shift_events(build_timeline(words, phone_map, mapping, default), args.offset)
        for spk, words in speakers.items()
    }

    speaking = {spk: speaking_intervals(words, args.offset) for spk, words in speakers.items()}
    for spk in timelines:
        others: List[Tuple[float, float]] = []
        for other_spk, intervals in speaking.items():
            if other_spk != spk:
                others.extend(intervals)
        others.sort()
        merged_others: List[Tuple[float, float]] = []
        for s, e in others:
            if merged_others and s <= merged_others[-1][1] + 0.001:
                merged_others[-1] = (merged_others[-1][0], max(merged_others[-1][1], e))
            else:
                merged_others.append((s, e))
        timelines[spk] = enforce_silence_between_speakers(timelines[spk], merged_others)

    output = {spk: to_json(evs) for spk, evs in timelines.items()}
    Path(args.output_json).write_text(json.dumps(output, indent=2, ensure_ascii=False),
                                      encoding="utf-8")

    print(f"✓ Timelines générées pour {len(output)} speaker(s) (offset {args.offset:+.3f}s).")
    for spk, evs in output.items():
        print(f"  {spk}: {len(evs)} événements")
    if skipped:
        print(f"  ({skipped} mot(s) sans timestamps ignoré(s))")
    if UNMAPPED:
        top = ", ".join(f"{p}×{n}" for p, n in UNMAPPED.most_common(10))
        print(f"  Phonèmes absents de la config (→ '{default}') : {top}")


if __name__ == "__main__":
    main()
