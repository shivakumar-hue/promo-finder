#!/usr/bin/env python3
"""
promo_finder.py (v2) - finds 20-60s promo clips in timestamped microdrama screenplays.

Pipeline
  1. PARSE     screenplay .txt -> typed, timed blocks (camera / action / sfx / dialogue)
  2. QA        timeline sanity checks; the results feed generation (long camera gaps are blocked)
  3. GENERATE  every legal window (start/end on shot cuts) of 20-60s, plus same-episode and
               cross-episode stitched promos (setup segment + payoff segment)
  4. SCORE     hook (first 3s) / cliffhanger (ending type) / standalone / energy / pacing / length
               Nothing in the scorer is specific to one show: no character names, no episode priors.
  5. SELECT    scene-level de-dup, near-duplicate hook de-dup, diminishing returns per episode
  6. VERIFY    every quoted line must be verbatim, inside the clip, and in the right zone
               (hook in the first 3s, cliffhanger in the last ~6s); no dialogue sliced by a cut
  7. EXPLAIN   "why it works" line per promo from the detected features
  8. (optional) --llm: LLM re-rank + rewrite of the "why" line; the verifier still runs afterwards

Usage:  python promo_finder.py /path/to/episodes_dir --out out_dir [--top 30] [--llm]
Outputs: promos.json (source of truth), promos.csv (generated from it), REPORT.md, data_qa.json
"""
import re, os, json, csv, glob, argparse
from dataclasses import dataclass, field

MIN_LEN, MAX_LEN = 20.0, 60.0
HOOK_WINDOW, CLIFF_WINDOW = 3.0, 6.0
MAX_CAMERA_GAP = 1.5        # a window may not contain a longer stretch with no shot

# ───────────────────────────── 1. PARSE ─────────────────────────────
TS = re.compile(r"Start:\s*\((\d+):(\d+(?:\.\d+)?)\)\s*\|\s*End:\s*\((\d+):(\d+(?:\.\d+)?)\)")
HDR = re.compile(r"^\*\*(.+?):\*\*\s*$")

def t2s(m, s): return int(m) * 60 + float(s)
def s2t(x):
    m = int(x // 60); return f"{m:02d}:{x - 60*m:05.2f}"

@dataclass
class Block:
    kind: str; start: float; end: float; text: str
    speaker: str = ""; tags: list = field(default_factory=list)
    offscreen: bool = False; scene: int = 0

@dataclass
class Episode:
    num: int; logline: str; blocks: list; scenes: list; path: str = ""; continuity: str = ""
    @property
    def duration(self): return max([b.end for b in self.blocks] + [s[3] for s in self.scenes])

def parse_episode(path):
    raw = open(path, encoding="utf-8-sig").read(); lines = raw.splitlines()
    nm = re.search(r"(?im)^EPISODE NUMBER:\s*Episode\s*(\d+)\s*$", raw)
    if not nm: raise ValueError(f"Missing EPISODE NUMBER header: {path}")
    num = int(nm.group(1))
    lg = re.search(r"(?ims)^EPISODE LOGLINE:\s*(.*?)(?=^\s*(?:CONTINUITY NOTES:|---)|\Z)", raw)
    cn = re.search(r"(?ims)^CONTINUITY NOTES:\s*(.*?)(?=^\s*---|\Z)", raw)
    blocks, scenes, scene_idx, cur, i = [], [], 0, {}, 0
    while i < len(lines):
        ln = lines[i].strip()
        m = re.match(r"^SCENE\s+(\d+)\s*$", ln, re.I)
        if m: scene_idx = int(m.group(1)); cur = {"idx": scene_idx}; i += 1; continue
        m = re.match(r"^scene heading:\s*(.+)", ln, re.I)
        if m: cur["heading"] = m.group(1); i += 1; continue
        m = re.match(r"^scene start:\s*(\d+):(\d+(?:\.\d+)?)", ln, re.I)
        if m: cur["start"] = t2s(*m.groups()); i += 1; continue
        m = re.match(r"^scene end:\s*(\d+):(\d+(?:\.\d+)?)", ln, re.I)
        if m:
            cur["end"] = t2s(*m.groups())
            scenes.append((cur["idx"], cur.get("heading", ""), cur.get("start", 0), cur["end"])); i += 1; continue
        h = HDR.match(ln)
        if h:
            label = h.group(1).strip(); j = i + 1
            while j < len(lines) and not lines[j].strip(): j += 1
            t = TS.search(lines[j]) if j < len(lines) else None
            if not t: i += 1; continue
            s = t2s(t.group(1), t.group(2)); e = t2s(t.group(3), t.group(4)); j += 1; txt = []
            while j < len(lines) and lines[j].strip() and not HDR.match(lines[j].strip()) \
                    and not re.match(r"^(SCENE\b|--- SCRIPT)", lines[j].strip()):
                txt.append(lines[j].strip()); j += 1
            text = " ".join(txt); up = label.upper()
            if up == "CAMERA": b = Block("camera", s, e, text)
            elif up == "ACTION": b = Block("action", s, e, text)
            elif up.startswith("SOUND"): b = Block("sfx", s, e, text)
            else:
                groups = re.findall(r"\(([^)]*)\)", label)
                name = re.sub(r"\s*\(.*", "", label).strip()
                OS = ("O.S.", "O.S. / CONTINUES", "V.O.", "OFF-SCREEN")
                os_ = any(g.strip().upper() in OS for g in groups) or "OFF-SCREEN" in up
                tags = [w.strip().lower() for g in groups if g.strip().upper() not in OS for w in g.split(",")]
                b = Block("dialogue", s, e, text.strip('"“” '), name, tags, os_)
            b.scene = scene_idx; blocks.append(b); i = j; continue
        i += 1
    blocks.sort(key=lambda b: (b.start, b.end))
    return Episode(num, " ".join(lg.group(1).split()) if lg else "", blocks, scenes, path,
                   " ".join(cn.group(1).split()) if cn else "")

HONORIFICS={"THE","LADY","LORD","SIR","DAME","MR","MRS","MS","DR","MADAM","FATHER"}
ROLE_WORDS={"MOTHER","FATHER","SON","DAUGHTER","BROTHER","SISTER","NOBLEMAN","NOBLEWOMAN","GUARD","SENTRY","MAID","PRIEST","CONSTABLE","VILLAGER","THUG","GROOM","BRIDE","SERVANT","OFFICER","VOICE","INFORMANT","SCOUT","FIGHTER","WOMAN","MAN","ELDER","FORMER"}
FAMILY_ROLE_WORDS={"MOTHER","FATHER","SON","DAUGHTER","BROTHER","SISTER"}
DESCRIPTOR_WORDS={"MYSTERIOUS","BEARDED","BLOND","SCARRED","TALL","ARISTOCRATIC","ELDER","OLD","YOUNG","BALD"}

def speaker_tokens(name):
    """Normalize names generically; discard delivery/offscreen parentheticals and honorifics."""
    base=re.sub(r"\([^)]*\)"," ",name.upper()).replace("’","'")
    words=re.findall(r"[A-Z]+",base)
    return frozenset(w for w in words if w not in HONORIFICS)

def canonical_speaker(name):
    """Return a generic normalized label; alias decisions are handled by speaker_alias()."""
    return " ".join(sorted(speaker_tokens(name)))

def speaker_alias(a,b):
    """Conservatively match exact normalized labels or meaningful name-token subsets."""
    A,B=speaker_tokens(a),speaker_tokens(b)
    if not A or not B:return False
    # Do not merge family roles with the relative whose name appears inside the label.
    if (A|B)&FAMILY_ROLE_WORDS:return False
    meaningful_a=A-ROLE_WORDS-DESCRIPTOR_WORDS
    meaningful_b=B-ROLE_WORDS-DESCRIPTOR_WORDS
    if not meaningful_a or not meaningful_b:return False
    # Differing honorifics plus only a shared surname is too ambiguous (e.g. Lady/Lord Ashcroft).
    ta=re.findall(r"[A-Z]+",a.upper());tb=re.findall(r"[A-Z]+",b.upper())
    titles_a=set(ta)&HONORIFICS;titles_b=set(tb)&HONORIFICS
    if titles_a and titles_b and titles_a!=titles_b and len(meaningful_a&meaningful_b)<2:return False
    if A==B:return True
    return meaningful_a<=meaningful_b or meaningful_b<=meaningful_a

# ───────────────────────────── 2. DATA QA ─────────────────────────────
def data_qa(ep):
    issues = []; cams = [b for b in ep.blocks if b.kind == "camera"]
    for a, b in zip(cams, cams[1:]):
        if b.start - a.end > 0.6: issues.append(f"camera gap {s2t(a.end)}->{s2t(b.start)} ({b.start-a.end:.1f}s no shot)")
        if a.end - b.start > 0.6: issues.append(f"camera overlap {s2t(b.start)}-{s2t(a.end)}")
    dl = [b for b in ep.blocks if b.kind == "dialogue"]
    for a, b in zip(dl, dl[1:]):
        if b.start < a.end - 0.05: issues.append(f"dialogue overlap '{a.speaker}'/'{b.speaker}' at {s2t(b.start)}")
    for d in dl:
        if d.end - d.start < 0.3: issues.append(f"very short dialogue window {s2t(d.start)}")
        if len(d.text.split()) / max(d.end - d.start, 0.01) > 5.5: issues.append(f"line too fast for speech at {s2t(d.start)}")
    if ep.scenes and ep.blocks and max(b.end for b in ep.blocks) > ep.scenes[-1][3] + 0.5: issues.append("blocks extend past last scene end")
    if ep.scenes and abs(ep.scenes[0][2]) > 0.01: issues.append(f"first scene starts at {s2t(ep.scenes[0][2])}, not 00:00")
    for a,b in zip(ep.scenes,ep.scenes[1:]):
        if abs(a[3]-b[2]) > 0.02: issues.append(f"scene boundary mismatch {s2t(a[3])}/{s2t(b[2])}")
    if not ep.scenes: issues.append("no scene timing headers; runtime falls back to timed blocks")
    return issues

def camera_gaps(ep):
    cams = sorted((b for b in ep.blocks if b.kind == "camera"), key=lambda b: b.start)
    return [(a.end, b.start) for a, b in zip(cams, cams[1:]) if b.start - a.end > MAX_CAMERA_GAP]

def window_qa_flags(gaps, S, E):
    overlapping = [(a, b) for a, b in gaps if a < E and b > S]
    if not overlapping: return []
    longest = max(b-a for a, b in overlapping)
    total = sum(min(E, b)-max(S, a) for a, b in overlapping)
    return [f"Camera notes leave {len(overlapping)} gap(s) in range (longest {longest:.1f}s; {total:.1f}s total overlap); verify against footage."]

# ───────────────────────────── 3. LEXICONS (generic drama, no show-specific words) ─────────────────────────────
def has(text, words): return any(re.search(r"\b" + w, text) for w in words)
def count(text, words): return sum(1 for w in words if re.search(r"\b" + w, text))

INTENSE_TAG = ("scream","shout","fur","rag","roar","vicious","venom","shriek","hyster","boom","outrag","accus","agoniz",
               "snarl","thunder","lethal","threat","imperious","cruel","scorn","desperat","panic","terrif","blood","cold","menac",
               "seeth","ice","trembl","broken","defian","stunn","incredul","horrif")
STAKES = ["disown","dead to","revoke","gallows","hang","blood","kill","betray","stole","planted","poison","witch","impossible",
          "abandon","get out","guards","arrest","prisoner","imprison","thief","lie","lied","truth","secret","traitor","murder",
          "forgive","vow","swear","pregnan","divorce","fired","expose","confess","proof","evidence","alive","dead","die","death",
          "marry","wedding","fiancé","fiance","husband","wife","heir","inherit","title","crown","trial","sentence","ruin","destroy"]
REVEAL_Q = ["who are you","who sent","what did you","you knew","you're not","you are not","how long","why did you","what have you",
            "you lied","you did this","it was you","all along","never loved","not yours","not mine","she's alive","he's alive","betrayed"]
VIOLENCE = ["slap","shove","snatch","wrench","fling","hurl","slam","smash","crash","punch","strike","struck","drag","seiz","grab",
            "lunge","tackle","shatter","stab","kick","throws","hurls","collaps","explo","burst","breach","fireball","gunshot","gunfire",
            "dagger","choke","knock","tiptoes","against the wall","pins"]
SHOCK_VIS = ["gasp","freeze","frozen","stunned","horrif","terror","petrif","aghast","wide eyes","eyes widen","jaw drops","realiz",
             "realis","smirk","triumphant smile","cold smile","vindictive","stares in disbelief","drained of","color drain","bulging"]
ARRIVAL = ["burst","bursts","emerge","enters","storms in","appears","steps into","stands in the doorway","doors swing","doors fly",
           "doors explode","kicks open","crashes through","breaches","arrives","walks in","silhouett","armed","soldiers","guards rush",
           "steps out","revealed","reveals","uncover","discover","through the crack"]
OBJECT_REVEAL = ["letter","ring","locket","document","scroll","seal","vial","poison","blood","knife","dagger","gun","photo","ledger","proof","contract","dossier","top secret","stamped","evidence","confession","testimony"]
RESOLVE = ["thank you","thanks","i've got you","safe now","kiss","embrace","i accept","i love you","we will face it together","tomorrow, we begin",
           "tender","contented","radiant","warm smile","rests her head","rests his head","nestle","hand-in-hand","walk away","walks away",
           "disappear into","fade out","fades out","sunlight","grateful","relief","laugh","smiles gently","gently smiles","softly smiles",
           "exchange triumphant smiles","drift","retire","goodnight","sleep"]
MUSIC_ONLY = ["swell","drone","strings","orchestral","score","cello","violin","organ","crescendo","chord","melod","music"]
BACKREF = ["as i said","as i told","like i said","remember when","last night","yesterday","that day","again","as before","you promised",
           "earlier","back then","the other day"]
WEAK_OPEN = re.compile(r"^(\.\.\.|and\b|then\b|but\b|so\b|because\b|yes\b|no\b|yes,|no,|okay\b|well\b|fine\b|it was\b|it is\b|that is\b|that was\b|he\b|she\b|they\b|we\b)", re.I)
RELATION = ["sister","brother","father","mother","husband","wife","family","daughter","son","fiancé","fiance","groom","bride",
            "duke","duchess","king","queen","lord","lady","master","servant","boss","ex","wedding","marry","married","soldier","veteran",
            "stranger","enemy","rival","lover","child","baby","heir"]

def tag_hit(tags): return any(any(k in t for k in INTENSE_TAG) for t in tags)

def line_intensity(b):
    s = 0.0; t = b.text.lower()
    if tag_hit(b.tags): s += 0.4
    if "!" in b.text: s += 0.2
    if "?" in b.text: s += 0.1
    if b.text.endswith(("—", "-", "...")): s += 0.05
    s += min(0.4, 0.15 * count(t, STAKES)); s += 0.25 * has(t, REVEAL_Q)
    return min(1.0, s)

def visual_intensity(b):
    t = b.text.lower()
    return min(1.0, 0.30 * count(t, VIOLENCE) + 0.25 * count(t, SHOCK_VIS) + 0.2 * count(t, ARRIVAL))

def events_in(ep, S, E):
    return [b for b in ep.blocks if b.kind != "camera" and b.start < E - 1e-6 and b.end > S + 1e-6]
def cams_in(ep, S, E):
    return [b for b in ep.blocks if b.kind == "camera" and b.start < E - 1e-6 and b.end > S + 1e-6]

# ───────────────────────────── 4. SCORING ─────────────────────────────
def valid_bounds(S, E, dls):
    """no dialogue sliced by a cut, no cut inside an unfinished sentence"""
    for d in dls:
        if d.start < S - 0.15 and d.end > S + 0.15: return False
        if d.start < E - 0.15 and d.end > E + 0.15: return False
    for a, b in zip(dls, dls[1:]):                       # '...' / '...' continuation across the cut
        if a.text.endswith("...") and b.text.startswith("..."):
            if a.end <= S + 0.15 and S <= b.start + 0.2 and b.start - a.end < 6: return False
    return True

def hook_score(ep, S):
    """Look at everything that happens in [S, S+3]: first line, first action, first shot."""
    zone = [b for b in ep.blocks if b.start < S + HOOK_WINDOW and b.end > S + 0.01 and b.start >= S - 0.15]
    best, label, why = 0.0, None, ""
    for b in zone:
        if b.kind == "dialogue":
            v = line_intensity(b); w = "an intense line"
            if b.start <= S + 1.5: v += 0.15
            if WEAK_OPEN.match(b.text): v -= 0.3; w = "a dependent reply"
            nw = len(b.text.split())
            if nw <= 2: v -= 0.3          # "Arrested?" / "And?!" mean nothing to a new viewer
            elif nw == 3: v -= 0.12
            if has(b.text.lower(), REVEAL_Q): w = "a reveal line"
            elif "?" in b.text and v >= 0.3: w = "a confrontational question"
            elif "!" in b.text: w = "a shouted line"
        elif b.kind in ("action", "camera"):
            v = 0.9 * visual_intensity(b); w = "a violent/visual beat"
            if b.kind == "camera": v *= 0.7
        else:
            v = 0.15 * (b.kind == "sfx" and has(b.text.lower(), ["crash","scream","gunshot","slam","shatter","explo"])); w = "a loud sound"
        if v > best: best, label, why = v, b, w
    if label is None: return 0.0, None, ""
    if not any(b.kind == "dialogue" for b in zone): best *= 0.85
    return max(0.0, min(1.0, best)), label, why

def end_type(ep, evs, cams, S, E):
    """Classify how the clip ends. Returns (score, type, label_block). Show-independent."""
    zone = [b for b in evs if b.end >= E - CLIFF_WINDOW]
    zc = [b for b in cams if b.end >= E - CLIFF_WINDOW]
    if not zone and not zc: return 0.0, "dead end", None
    text = " ".join(b.text.lower() for b in zone + zc)
    dl = [b for b in zone if b.kind == "dialogue"]
    lastd = dl[-1] if dl else None
    non_sfx = [b for b in zone if b.kind in ("dialogue", "action")]
    last = max(non_sfx, key=lambda b: b.end) if non_sfx else None
    final_cam = max(zc, key=lambda b: b.end) if zc else None
    s, kind, lab_vis = 0.0, "weak", False
    # (a) interrupted / trailing line, line left open
    if lastd and E - lastd.end < 3.5:
        t = lastd.text.rstrip()
        if t.endswith(("—", "-")): s, kind = max(s, 0.8), "interrupted line"
        elif t.endswith("..."): s, kind = max(s, 0.55), "trailing line"
        if t.endswith("?"):
            q = 0.5 + (0.1 if line_intensity(lastd) >= 0.4 else 0) + (0.1 if has(t.lower(), REVEAL_Q) else 0)
            # the question must go unanswered: nothing but reaction after it
            if q > s: s, kind = q, "unanswered question"
        if t.endswith("!") and line_intensity(lastd) >= 0.5 and s < 0.45: s, kind = 0.45, "threat/ultimatum"
        if has(t.lower(), ["tomorrow","tonight","by dawn","two days","one week","before the","or else","you have until","countdown"]) \
           and has(t.lower(), STAKES) and s < 0.6: s, kind = 0.6, "ticking clock"
    # (b) a visual/action beat in the last seconds: arrival, reveal, violence, shocked reaction
    vis_blocks = [b for b in zone if b.kind == "action" and b.start < E - 0.8] + [b for b in zc if b.start < E - 0.8]
    vtxt = " ".join(b.text.lower() for b in vis_blocks)
    arr = count(vtxt, ARRIVAL); vio = count(vtxt, VIOLENCE); shock = count(vtxt, SHOCK_VIS); obj = count(vtxt, OBJECT_REVEAL)
    v = min(0.85, 0.22 * arr + 0.18 * vio + 0.22 * shock + 0.1 * obj)
    trig = max(vis_blocks, key=lambda b: (visual_intensity(b) + 0.2 * count(b.text.lower(), OBJECT_REVEAL), b.kind == "action", b.end)) if vis_blocks else None
    if trig is not None and trig.kind == "camera":                       # prefer the ACTION block describing the same beat
        sib = [b for b in vis_blocks if b.kind == "action" and abs(b.start - trig.start) < 2.5]
        if sib: trig = max(sib, key=visual_intensity)
    if v > s:
        s = v; lab_vis = True
        kind = ("reveal/arrival" if arr >= max(vio, shock) and arr else "violent action" if vio >= shock and vio else "shocked/smug reaction beat")
    # reaction shot after a hard line boosts it
    if lastd and final_cam and final_cam.start >= lastd.start - 0.2 and has(final_cam.text.lower(), SHOCK_VIS) and E - lastd.end > 0.5:
        s = max(s, 0.55) + 0.12; kind = kind if kind != "weak" else "reaction beat after a hard line"
    if lastd and has(lastd.text.lower(), REVEAL_Q) and s < 0.6: s, kind = 0.6, "reveal line"
    # penalties: resolved, mundane, music-only endings
    pen = min(0.6, 0.2 * count(text, RESOLVE))
    music = has(text, MUSIC_ONLY)
    only_sfx_last = (not non_sfx) or (last is not None and E - last.end > 4.0)
    if music and only_sfx_last: pen += 0.25
    if lastd and lastd.text.rstrip().endswith(".") and not has(lastd.text.lower(), STAKES) and line_intensity(lastd) < 0.2 and s < 0.7:
        pen += 0.15; kind = kind if kind != "weak" else "closed statement"
    if E - max([b.end for b in evs] + [S]) > 2.5: pen += 0.15      # dead tail
    # a clip that ends on an action that resolves in the same beat ("Are you hurt?") is not a cliff
    if lastd and lastd.text.rstrip().endswith("?") and has(lastd.text.lower(), ["are you ok","are you hurt","are you alright","okay?","you alright"]): pen += 0.3
    s = max(0.0, min(1.0, s - pen))
    if lab_vis and trig is not None and kind in ("reveal/arrival","violent action","shocked/smug reaction beat"): label = trig
    elif lastd and kind in ("interrupted line","trailing line","unanswered question","threat/ultimatum","ticking clock","reveal line","reaction beat after a hard line"): label = lastd
    else: label = last or lastd
    peak = label.end if label is not None else E
    s *= max(0.55, 1.0 - 0.1 * max(0.0, E - peak - 2.0))     # peak recency: end near the peak, not after the denouement
    return s, kind, label

def standalone_score(ep, evs, S, E, first_scene_start):
    dl = [b for b in evs if b.kind == "dialogue"]
    if not dl: return 0.0, "no speech"
    spk=[]
    for b in dl:
        if not b.offscreen and not any(speaker_alias(b.speaker,old) for old in spk):spk.append(b.speaker)
    txt = " ".join(b.text.lower() for b in dl) + " " + " ".join(b.text.lower() for b in evs if b.kind == "action")
    s = 0.0
    s += 0.25 if len(spk) >= 2 else 0.0
    s += min(0.25, 0.07 * count(txt, RELATION))                       # relationships are spelled out
    s += 0.15 if not WEAK_OPEN.match(dl[0].text) else 0.0
    s += 0.15 if any(abs(S - sc[2]) < 0.3 for sc in ep.scenes) else 0.0   # starts at a scene start: setting is established
    scenes_touched = {b.scene for b in evs}
    s += 0.1 if len(scenes_touched) == 1 else 0.0
    s -= min(0.25, 0.1 * count(txt, BACKREF))                          # refers to things the viewer never saw
    s += 0.1 if len(dl) >= 4 else 0.0
    return max(0.0, min(1.0, s)), ("two+ speakers" if len(spk) >= 2 else "single speaker")

def energy_score(evs, cams, S, E):
    dl = [b for b in evs if b.kind == "dialogue"]
    inten = (sum(line_intensity(b) for b in dl) / len(dl)) if dl else 0.0
    turns = sum(1 for a, b in zip(dl, dl[1:]) if a.speaker != b.speaker) / max(1.0, (E - S) / 10)
    cuts = len(cams) / max(1.0, (E - S) / 10)                            # shots per 10s
    vis = sum(visual_intensity(b) for b in evs if b.kind == "action") / max(1, len([b for b in evs if b.kind == "action"]))
    return min(1.0, 0.4 * inten + 0.25 * min(1.0, turns / 3) + 0.15 * min(1.0, cuts / 4) + 0.2 * vis)

def length_score(L):
    if 28 <= L <= 50: return 1.0
    if L < 28: return 0.5 + 0.5 * (L - 20) / 8
    return max(0.5, 1.0 - 0.5 * (L - 50) / 10)

def pacing_penalty(evs, S, E):
    """dead air inside the clip: stretches > 4s with no dialogue"""
    marks = sorted([(max(b.start, S), min(b.end, E)) for b in evs if b.kind == "dialogue"])
    t, worst = S, 0.0
    for a, b in marks: worst = max(worst, a - t); t = max(t, b)
    worst = max(worst, E - t)
    return min(0.2, 0.05 * max(0.0, worst - 4.0))

W = dict(hook=0.28, cliff=0.32, standalone=0.18, energy=0.14, length=0.08)

def score_window(ep, S, E, dls, gaps, fs):
    if not valid_bounds(S, E, dls): return None
    evs, cams = events_in(ep, S, E), cams_in(ep, S, E)
    if len(evs) < 4: return None
    if min(b.start for b in evs) - S > 3.0 or E - max(b.end for b in evs) > 3.0: return None
    h, hb, hwhy = hook_score(ep, S); c, ckind, cb = end_type(ep, evs, cams, S, E)
    st, stwhy = standalone_score(ep, evs, S, E, fs); en = energy_score(evs, cams, S, E); ln = length_score(E - S)
    qaf = window_qa_flags(gaps, S, E)
    gap_seconds=sum(min(E,g1)-max(S,g0) for g0,g1 in gaps if g0<E and g1>S)
    overlapping_gap_count=sum(1 for g0,g1 in gaps if g0<E and g1>S)
    qa_penalty=min(0.18,0.015*overlapping_gap_count + 0.012*gap_seconds)
    tot = W["hook"]*h + W["cliff"]*c + W["standalone"]*st + W["energy"]*en + W["length"]*ln - pacing_penalty(evs, S, E) - qa_penalty
    return dict(hook=h, cliff=c, standalone=st, energy=en, length=ln, total=tot,
                hook_block=hb, hook_why=hwhy, cliff_block=cb, cliff_kind=ckind, stand_why=stwhy,
                qa_flags=qaf)

# ───────────────────────────── 5. GENERATE ─────────────────────────────
def bounds(ep):
    cams = [b for b in ep.blocks if b.kind == "camera"]
    starts = sorted({round(c.start, 2) for c in cams} | {s[2] for s in ep.scenes})
    ends = sorted({round(c.end, 2) for c in cams} | {s[3] for s in ep.scenes} | {round(ep.duration, 2)})
    return starts, ends

def hook_starts(ep, starts, bucket=4.0):
    """Keep the strongest real cut-in per short time bucket to avoid near-duplicate windows."""
    chosen={}
    for S in starts:
        h,hb,hw=hook_score(ep,S)
        if h < 0.15: continue
        k=int(S//bucket)
        if k not in chosen or h>chosen[k][1]: chosen[k]=(S,h,hb,hw)
    return sorted(chosen.values(),key=lambda x:x[0])

def generate(ep):
    dls = sorted([b for b in ep.blocks if b.kind == "dialogue"], key=lambda b: b.start)
    gaps = camera_gaps(ep); starts, ends = bounds(ep); out = []
    end_quality = {}
    for E in ends:
        es=max(0.0,E-CLIFF_WINDOW)
        end_quality[E]=end_type(ep,events_in(ep,es,E),cams_in(ep,es,E),max(0.0,E-MAX_LEN),E)[0]
    for S,h,_,_ in hook_starts(ep,starts):
        possible=[E for E in ends if MIN_LEN <= E-S <= MAX_LEN]
        if not possible: continue
        chosen={min(possible,key=lambda E:abs((E-S)-target)) for target in (24,30,36,42,48,54,60)}
        chosen.update(sorted(possible,key=lambda E:(-end_quality[E],abs((E-S)-36)) )[:3])
        for E in sorted(chosen):
            if not (MIN_LEN <= E - S <= MAX_LEN): continue
            r = score_window(ep, S, E, dls, gaps, 0)
            if r: out.append(dict(ep=ep.num, segs=[(S, E)], dur=E - S, kind="continuous", **r))
    return out, dls, gaps

def short_segments(ep, dls, gaps, lo=6, hi=22):
    starts, ends = bounds(ep); out = []
    end_quality={}
    for E in ends:
        es=max(0.0,E-CLIFF_WINDOW)
        end_quality[E]=end_type(ep,events_in(ep,es,E),cams_in(ep,es,E),max(0.0,E-hi),E)[0]
    for S,h,hb,hw in hook_starts(ep,starts):
        possible=[E for E in ends if lo <= E-S <= hi]
        if not possible: continue
        chosen={min(possible,key=lambda E:abs((E-S)-target)) for target in (8,12,16,20)}
        chosen.update(sorted(possible,key=lambda E:(-end_quality[E],abs((E-S)-14)))[:3])
        for E in sorted(chosen):
            if not (lo <= E - S <= hi) or not valid_bounds(S, E, dls): continue
            evs, cams = events_in(ep, S, E), cams_in(ep, S, E)
            if len(evs) < 3: continue
            c, ck, cb = end_type(ep, evs, cams, S, E)
            qaf=window_qa_flags(gaps,S,E)
            out.append(dict(S=S, E=E, h=h, hb=hb, hw=hw, c=c, ck=ck, cb=cb, evs=evs, cams=cams, qa_flags=qaf))
    return out

def stitched(ep, dls, gaps, other_eps):
    """Same-episode (setup, payoff) and cross-episode (episode N setup + episode N+k payoff) stitches."""
    segs = {ep.num: short_segments(ep, dls, gaps)}
    out = []
    A = sorted(segs[ep.num], key=lambda x: -x["h"])[:10]
    for k in (0, 1, 2):
        tgt = ep if k == 0 else other_eps.get(ep.num + k)
        if tgt is None: continue
        if tgt.num not in segs:
            tdl = sorted([b for b in tgt.blocks if b.kind == "dialogue"], key=lambda b: b.start)
            segs[tgt.num] = short_segments(tgt, tdl, camera_gaps(tgt))
        B = sorted(segs[tgt.num], key=lambda x: -x["c"])[:10]
        for a in A:
            for b in B:
                asp={e.speaker for e in a["evs"] if e.kind=="dialogue" and not e.offscreen}
                bsp={e.speaker for e in b["evs"] if e.kind=="dialogue" and not e.offscreen}
                if not any(speaker_alias(x,y) for x in asp for y in bsp): continue
                if k == 0 and b["S"] - a["E"] < 6: continue
                L = (a["E"] - a["S"]) + (b["E"] - b["S"])
                if not (MIN_LEN <= L <= MAX_LEN): continue
                evs = a["evs"] + b["evs"]
                st, sw = standalone_score(ep, evs, a["S"], b["E"], 0)
                st = max(0.0, st - (0.08 if k == 0 else 0.15))
                en = energy_score(evs, a["cams"] + b["cams"], a["S"], a["S"] + L)
                tot = W["hook"]*a["h"] + W["cliff"]*b["c"] + W["standalone"]*st + W["energy"]*en + W["length"]*length_score(L) - (0.04 if k == 0 else 0.08)
                qaf=a["qa_flags"]+b["qa_flags"]
                out.append(dict(ep=ep.num, segs=[(a["S"], a["E"]), (b["S"], b["E"])], seg_eps=[ep.num, tgt.num], dur=L,
                                kind="stitched" if k == 0 else "cross-episode", hook=a["h"], cliff=b["c"], standalone=st,
                                energy=en, length=length_score(L), total=tot, hook_block=a["hb"], hook_why=a["hw"],
                                cliff_block=b["cb"], cliff_kind=b["ck"], stand_why=sw,qa_flags=qaf))
    return out

# ───────────────────────────── 6. SELECT ─────────────────────────────
def seg_eps(c): return c.get("seg_eps", [c["ep"]] * len(c["segs"]))
def overlap(a, b):
    tot = 0.0
    for (s1, e1), p in zip(a["segs"], seg_eps(a)):
        for (s2, e2), q in zip(b["segs"], seg_eps(b)):
            if p == q: tot += max(0.0, min(e1, e2) - max(s1, s2))
    return tot / min(a["dur"], b["dur"])

def norm(t): return re.sub(r"\W+", " ", (t or "").lower()).strip()

def select(cands, top=30, per_ep=3, ep_decay=0.04):
    """greedy with diminishing returns: each already-picked clip from the same episode costs ep_decay"""
    pool = sorted(cands, key=lambda c: -c["total"]); kept, cnt = [], {}
    while pool and len(kept) < top:
        best, bi = None, -1
        for i, c in enumerate(pool[:400]):
            eps_c = set(seg_eps(c))
            adj = c["total"] - ep_decay * sum(cnt.get(e, 0) for e in eps_c)
            if best is None or adj > best[0]: best, bi = (adj, c), i
        adj, c = best; pool.pop(bi)
        if any(cnt.get(e, 0) >= per_ep for e in set(seg_eps(c))): continue
        if any(overlap(c, k) > 0.2 for k in kept): continue
        if any(norm(getattr(c["hook_block"], "text", "")) == norm(getattr(k["hook_block"], "text", "")) for k in kept): continue
        # adjacent windows in the same scene are the same moment
        same_scene = [k for k in kept if k["ep"] == c["ep"] and k["kind"] == "continuous" and c["kind"] == "continuous"
                      and min(abs(k["segs"][0][0] - c["segs"][0][0]), abs(k["segs"][0][1] - c["segs"][0][1])) < 12]
        if same_scene: continue
        # two picks that end on the same payoff beat are the same promo with a different run-up
        if any(set(seg_eps(k)[-1:]) == set(seg_eps(c)[-1:]) and abs(k["segs"][-1][1] - c["segs"][-1][1]) < 8 for k in kept): continue
        c["_adj"] = adj; kept.append(c)
        for e in set(seg_eps(c)): cnt[e] = cnt.get(e, 0) + 1
    return kept

# ───────────────────────────── 7. VERIFY + EXPLAIN ─────────────────────────────
def quote(b):
    if b is None: return ""
    return f'{b.speaker.title()}: "{b.text}"' if b.kind == "dialogue" else b.text

def verify(c, eps_by):
    """Programmatic checks. Returns list of problems (empty = pass)."""
    probs = []; segs = c["segs"]; seps = seg_eps(c)
    hb, cb = c["hook_block"], c["cliff_block"]
    s0 = segs[0][0]
    if hb is None: probs.append("no hook block")
    else:
        if not (s0 - 0.16 <= hb.start < s0 + HOOK_WINDOW and hb.end > s0): probs.append("hook not in first 3s")
        if not any(b is hb for b in eps_by[seps[0]].blocks): probs.append("hook text not in script")
    if cb is None: probs.append("no cliffhanger block")
    else:
        sl, el = segs[-1]; ee = eps_by[seps[-1]]
        if not (cb.start < el and cb.end > el - CLIFF_WINDOW - 0.5): probs.append("cliffhanger not in last ~6s")
        if cb.kind == "dialogue" and cb.end > el + 0.15: probs.append("cliffhanger line runs past the cut")
        if not any(b is cb for b in ee.blocks): probs.append("cliffhanger text not in script")
    for (s, e), n in zip(segs, seps):
        for d in [b for b in eps_by[n].blocks if b.kind == "dialogue"]:
            if (d.start < s - 0.15 < d.end - 0.3) or (d.start < e - 0.15 and d.end > e + 0.15): probs.append(f"dialogue sliced at {s2t(s)}/{s2t(e)}")
    if not (MIN_LEN - 0.01 <= c["dur"] <= MAX_LEN + 0.01): probs.append("length out of 20-60s")
    return probs

def why_line(c):
    parts = []
    parts.append(f"Opens on {c['hook_why'] or 'action'}")
    parts.append(f"ends on {c['cliff_kind']}")
    parts.append("has context signals for a new viewer (heuristic; verify in footage)" if c["standalone"] >= 0.55 else "may rely on earlier context; check standalone clarity")
    tail = {"continuous": "", "stitched": " (stitched setup+payoff)", "cross-episode": " (cross-episode stitch)"}[c["kind"]]
    return "; ".join(parts) + tail

# ───────────────────────────── 8. LLM (optional) ─────────────────────────────
LLM_PROMPT = """You are a short-drama promo editor (ReelShort/DramaBox style). Judge each candidate clip cut from a screenplay.
Return ONLY a JSON list: [{"id":int,"hook":0-10,"cliffhanger":0-10,"standalone":0-10,"overall":0-10,"why":"one line","problem":"..."}]. Be harsh.
Only judge what is in the transcript; never invent lines."""
def llm_rerank(eps_by, cands, model="claude-sonnet-5-5"):
    import anthropic; client = anthropic.Anthropic(); items = []
    for i, c in enumerate(cands):
        txt = []
        for (s, e), n in zip(c["segs"], seg_eps(c)):
            for b in events_in(eps_by[n], s, e):
                if b.kind in ("dialogue", "action"):
                    txt.append(f"[{s2t(b.start)}] " + (f"{b.speaker}: {b.text}" if b.kind == "dialogue" else f"(action) {b.text}"))
            txt.append("--- cut ---")
        items.append(f"ID {i}\n" + "\n".join(txt))
    msg = client.messages.create(model=model, max_tokens=4000, messages=[{"role": "user", "content": LLM_PROMPT + "\n\n" + "\n\n".join(items)}])
    return json.loads(re.search(r"\[.*\]", msg.content[0].text, re.S).group(0))

# ───────────────────────────── main ─────────────────────────────
def fmt_segs(c): return " + ".join(f"Ep{n} {s2t(s)}–{s2t(e)}" for (s, e), n in zip(c["segs"], seg_eps(c)))

def to_record(c):
    return dict(rank=c["rank"], kind=c["kind"], duration_s=round(c["dur"], 1), score=round(c["total"], 3),
                segments=[dict(episode=n, start=s2t(s), end=s2t(e), start_s=round(s, 2), end_s=round(e, 2)) for (s, e), n in zip(c["segs"], seg_eps(c))],
                hook=quote(c["hook_block"]), hook_at=s2t(c["hook_block"].start) if c["hook_block"] else "",
                cliffhanger=quote(c["cliff_block"]), cliffhanger_type=c["cliff_kind"], why_it_works=c["why"],
                cliffhanger_at=s2t(c["cliff_block"].start) if c["cliff_block"] else "",
                scores={k: round(c[k], 2) for k in ("hook", "cliff", "standalone", "energy", "length")},
                verified=not c["problems"], problems=c["problems"], quality_flags=c.get("qa_flags",[]),
                **({"llm": c["llm"]} if "llm" in c else {}))

def csv_row(r):
    return [r["rank"],r["kind"]," + ".join(f'Ep{s["episode"]} {s["start"]}-{s["end"]}' for s in r["segments"]),
            r["duration_s"],r["score"],r["hook"],r["hook_at"],r["cliffhanger"],r["cliffhanger_type"],r["why_it_works"],r["verified"]]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("indir"); ap.add_argument("--out", default="out"); ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--per-ep", type=int, default=3); ap.add_argument("--llm", action="store_true")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    paths=glob.glob(os.path.join(a.indir,"**","*.txt"),recursive=True)
    eps = sorted((parse_episode(p) for p in paths), key=lambda e: e.num)
    eps_by = {e.num: e for e in eps}
    qa = {e.num: {"runtime_seconds":round(e.duration,2),"scene_count":len(e.scenes),"issues":data_qa(e)} for e in eps}
    with open(os.path.join(a.out, "data_qa.json"), "w",encoding="utf-8") as f: json.dump(qa,f,indent=1,ensure_ascii=False)
    pool = []
    for e in eps:
        c, dls, gaps = generate(e); pool += c + stitched(e, dls, gaps, eps_by)
    # Structural checks filter candidates before ranking; camera-note gaps remain review flags.
    for c in pool:
        c["why"] = ""; c["problems"] = verify(c, eps_by)
    eligible=[c for c in pool if not c["problems"]]
    ranked = select(eligible, a.top, a.per_ep)
    if a.llm:
        try:
            for r in llm_rerank(eps_by, ranked): ranked[r["id"]]["llm"] = r
            ranked.sort(key=lambda c: -c.get("llm", {}).get("overall", 0))
        except Exception as ex: print("LLM re-rank skipped:", ex)
    for i, c in enumerate(ranked, 1):
        c["rank"] = i; c["problems"] = verify(c, eps_by); c["why"] = (c.get("llm") or {}).get("why") or why_line(c)
    recs = [to_record(c) for c in ranked]
    json_path=os.path.join(a.out,"promos.json")
    with open(json_path,"w",encoding="utf-8") as f: json.dump(recs,f,indent=1,ensure_ascii=False)
    with open(json_path,encoding="utf-8") as f: csv_records=json.load(f)
    with open(os.path.join(a.out, "promos.csv"), "w", newline="",encoding="utf-8-sig") as f:      # generated from promos.json: one source of truth
        w = csv.writer(f); w.writerow(["rank","kind","segments","duration_s","score","hook","hook_at","cliffhanger","cliffhanger_type","why_it_works","verified"])
        for r in csv_records:w.writerow(csv_row(r))
    runtimes=[e.duration for e in eps]; season=sum(runtimes)
    runtime_report={"episodes_read":len(eps),"scored_candidates":len(pool),"structurally_valid_candidates":len(eligible),
                    "selected":len(recs),"verified_selected":sum(r["verified"] for r in recs),
                    "season_runtime_seconds":round(season,2),"season_runtime":s2t(season),
                    "season_runtime_hms":f"{int(season//3600):02d}:{int((season%3600)//60):02d}:{season%60:05.2f}",
                    "average_episode_seconds":round(season/max(1,len(eps)),2),
                    "episodes":[{"episode":e.num,"runtime":s2t(e.duration),"runtime_seconds":round(e.duration,2),"scene_count":len(e.scenes)} for e in eps]}
    per_ep={e.num:[c for c in ranked if e.num in set(seg_eps(c))] for e in eps}
    best_per_episode=[]
    for e in eps:
        options=[c for c in eligible if c["ep"]==e.num]
        if not options:
            best_per_episode.append({"episode":e.num,"in_top30":False,"top30_rank":None,"promo":None});continue
        best=max(options,key=lambda c:c["total"])
        clone=dict(best);clone["rank"]=best.get("rank",0);clone["problems"]=best.get("problems",[]);clone["why"]=why_line(clone)
        best_per_episode.append({"episode":e.num,"in_top30":id(best) in {id(c) for c in ranked},
                                 "top30_rank":next((c["rank"] for c in ranked if c is best),None),"promo":to_record(clone)})
    with open(os.path.join(a.out,"best_per_episode.json"),"w",encoding="utf-8") as f:json.dump(best_per_episode,f,indent=2,ensure_ascii=False)
    with open(os.path.join(a.out,"best_per_episode.csv"),"w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f);w.writerow(["episode","in_top30","top30_rank","kind","segments","duration_s","score","hook","cliffhanger_type","cliffhanger","verified","quality_flags"])
        for row in best_per_episode:
            r=row["promo"]
            if not r:w.writerow([row["episode"],False,"","","","","","","","",False,""]);continue
            w.writerow([row["episode"],row["in_top30"],row["top30_rank"] or "",r["kind"]," + ".join(f'Ep{s["episode"]} {s["start"]}-{s["end"]}' for s in r["segments"]),r["duration_s"],r["score"],r["hook"],r["cliffhanger_type"],r["cliffhanger"],r["verified"],"; ".join(r["quality_flags"])])
    runtime_report["episodes"]=[{"episode":e.num,"runtime":s2t(e.duration),"runtime_seconds":round(e.duration,2),"scene_count":len(e.scenes),
                                 "selected_promo_count":len(per_ep[e.num]),"best_selected_score":round(max((c["total"] for c in per_ep[e.num]),default=0),3),
                                 "logline":e.logline,"continuity_notes":e.continuity} for e in eps]
    with open(os.path.join(a.out,"episode_coverage.json"),"w",encoding="utf-8") as f: json.dump(runtime_report,f,indent=2,ensure_ascii=False)
    with open(os.path.join(a.out,"episode_coverage.csv"),"w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f);w.writerow(["episode","runtime","runtime_seconds","scene_count","selected_promo_count","best_selected_score","best_candidate_score","best_candidate_in_top30","data_qa_issue_count"])
        for e in eps:
            best=max((c for c in eligible if c["ep"]==e.num),key=lambda c:c["total"],default=None)
            w.writerow([e.num,s2t(e.duration),round(e.duration,2),len(e.scenes),len(per_ep[e.num]),round(max((c["total"] for c in per_ep[e.num]),default=0),3),round(best["total"],3) if best else "",bool(best and id(best) in {id(c) for c in ranked}),len(qa[e.num]["issues"])])
    print(f"{len(eps)} episodes, {len(pool)} candidates, {len(recs)} selected, {sum(r['verified'] for r in recs)} structurally verified; estimated runtime {runtime_report['season_runtime_hms']}")
    for r in recs:
        print(f'#{r["rank"]:>2} {fmt_segs(ranked[r["rank"]-1]):<48} {r["duration_s"]:5.1f}s {r["score"]:.2f} [{r["kind"]}]')
        print(f'     HOOK: {r["hook"][:100]}\n     END : [{r["cliffhanger_type"]}] {r["cliffhanger"][:110]}')
        if r["problems"]: print("     !!", r["problems"])

if __name__ == "__main__":
    main()
