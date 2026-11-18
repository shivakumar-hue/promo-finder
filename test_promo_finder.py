#!/usr/bin/env python3
"""Regression and output-integrity checks for Promo Finder v2.

Run: python test_promo_finder.py EPISODES_DIR --out OUTPUT_DIR
Uses only Python's standard library; validates known ending cases and checks
every selected quote against the correct source episode and time range.
"""
import argparse, csv, json, re, sys
from pathlib import Path
import promo_finder as p

def quote_parts(value):
    if ': "' not in value:return None, value
    speaker, tail=value.split(': "',1)
    return speaker, tail.rsplit('"',1)[0]

def source_block_exists(value, episode, start, end, expected_at=None, zone=None):
    speaker, text=quote_parts(value)
    for b in episode.blocks:
        if b.start >= end or b.end <= start:continue
        if expected_at is not None and abs(b.start-expected_at)>0.02:continue
        if speaker is not None:
            if b.kind!="dialogue" or b.speaker.title()!=speaker or b.text!=text:continue
        elif b.text!=text:continue
        if zone=="hook" and not (start-0.16<=b.start<start+p.HOOK_WINDOW):continue
        if zone=="cliff" and not (b.start<end and b.end>end-p.CLIFF_WINDOW-0.5):continue
        return True
    return False

def main():
    ap=argparse.ArgumentParser();ap.add_argument("episodes_dir",type=Path);ap.add_argument("--out",type=Path,required=True);a=ap.parse_args()
    files=sorted(a.episodes_dir.rglob("*.txt"));eps={}
    for f in files:
        e=p.parse_episode(str(f));eps[e.num]=e
    failures=[];passes=0
    def check(ok,label):
        nonlocal passes
        if ok:passes+=1;print("PASS",label)
        else:failures.append(label);print("FAIL",label)

    good=[(41,92.0,.50,"explosion / arrival"),(45,121.75,.50,"smirk after marriage ruling"),
          (50,78.5,.50,"Agnes discovers the secret"),(28,47.4,.50,"interrupted poisoning accusation"),
          (36,160.0,.50,"two-day deadline")]
    bad=[(15,86.0,.45,"music-led ending"),(10,94.0,.35,"resolved confession"),(5,110.5,.35,"soft resolved ending")]
    for n,E,threshold,label in good:
        e=eps[n];ev=p.events_in(e,max(0,E-30),E);cams=p.cams_in(e,max(0,E-30),E)
        score,kind,_=p.end_type(e,ev,cams,max(0,E-30),E)
        check(score>=threshold,f"positive cliffhanger Ep{n} {label}: {score:.2f} ({kind})")
    for n,E,threshold,label in bad:
        e=eps[n];ev=p.events_in(e,max(0,E-30),E);cams=p.cams_in(e,max(0,E-30),E)
        score,kind,_=p.end_type(e,ev,cams,max(0,E-30),E)
        check(score<=threshold,f"negative ending Ep{n} {label}: {score:.2f} ({kind})")

    check(p.speaker_alias("LADY JULIANA","JULIANA"),"generic honorific alias")
    check(p.speaker_alias("ALEXANDER","ALEXANDER ASHBURTON"),"name-token subset alias")
    check(not p.speaker_alias("LADY ASHCROFT","LORD ASHCROFT"),"do not merge conflicting titled people")
    check(not p.speaker_alias("ALEXANDER'S MOTHER","ALEXANDER"),"do not merge family-role reference with person")

    promos_path=a.out/"promos.json";csv_path=a.out/"promos.csv"
    check(promos_path.exists(),"promos.json exists")
    check(csv_path.exists(),"promos.csv exists")
    if promos_path.exists() and csv_path.exists():
        records=json.loads(promos_path.read_text(encoding="utf-8"))
        with csv_path.open(encoding="utf-8-sig",newline="") as f:rows=list(csv.DictReader(f))
        check(len(records)==len(rows),"CSV row count matches JSON source")
        if len(records)==len(rows):
            check(all(int(r["rank"])==j+1 and r["segments"]==" + ".join(
                f'Ep{s["episode"]} {s["start"]}-{s["end"]}' for s in records[j]["segments"])
                for j,r in enumerate(rows)),"CSV segment/rank fields generated from JSON")
        check(bool(records) and all(r["verified"] for r in records),f"all {len(records)} shortlisted promos pass structural verification")
        checked_quotes=0
        for r in records:
            if not r["segments"]:continue
            first,last=r["segments"][0],r["segments"][-1]
            h_ep=eps[int(first["episode"])];c_ep=eps[int(last["episode"])]
            h_at=p.t2s(*re.match(r"(\d+):(\d+(?:\.\d+)?)",r["hook_at"]).groups()) if r["hook_at"] else None
            c_at=p.t2s(*re.match(r"(\d+):(\d+(?:\.\d+)?)",r["cliffhanger_at"]).groups()) if r["cliffhanger_at"] else None
            if not source_block_exists(r["hook"],h_ep,first["start_s"],first["end_s"],h_at,"hook"):
                failures.append(f"rank {r['rank']} hook not verbatim in source episode/range");print("FAIL",failures[-1])
            elif not source_block_exists(r["cliffhanger"],c_ep,last["start_s"],last["end_s"],c_at,"cliff"):
                failures.append(f"rank {r['rank']} cliffhanger not verbatim in source episode/range");print("FAIL",failures[-1])
            else:checked_quotes+=1
        check(checked_quotes==len(records),f"episode-specific verbatim hook/cliffhanger checks ({checked_quotes}/{len(records)})")

    best_path=a.out/"best_per_episode.json"
    check(best_path.exists(),"per-episode fallback list exists")
    best_csv=a.out/"best_per_episode.csv"
    check(best_csv.exists(),"per-episode fallback CSV exists")
    if best_path.exists():
        best=json.loads(best_path.read_text(encoding="utf-8"))
        check(len(best)==len(eps),f"fallback list covers all {len(eps)} episodes")
        finale=[x for x in best if x["episode"] in (47,48,49,50)]
        check(len(finale)==4 and all(x["promo"] is not None for x in finale),"best candidates available for Episodes 47–50")
        if best_csv.exists():
            with best_csv.open(encoding="utf-8-sig",newline="") as f:best_rows=list(csv.DictReader(f))
            check(len(best_rows)==len(best),"fallback CSV row count matches JSON")

    print(f"\n{passes} checks passed; {len(failures)} failed.")
    return 1 if failures else 0

if __name__=="__main__":sys.exit(main())
