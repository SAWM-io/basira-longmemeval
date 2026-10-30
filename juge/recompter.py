"""Recompte l'exactitude LongMemEval à partir des réponses jugées publiées.

Aucune dépendance, aucun appel réseau, aucune clé : le script lit les verdicts
(autoeval_label) écrits par le juge officiel GPT-4o, question par question.

Usage :
  python3 -I recompter.py ../reponses/*.jsonl
  python3 -I recompter.py ../reponses/*.jsonl --references longmemeval_s_cleaned.json

Avec --references (jeu officiel, à télécharger chez ses auteurs), le détail par
type de question et pour les questions d'abstention est ajouté.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def lire(chemin: Path) -> list[dict]:
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Recompte l'exactitude LongMemEval.")
    ap.add_argument("fichiers", nargs="+")
    ap.add_argument("--references")
    a = ap.parse_args()

    types = {}
    if a.references:
        types = {e["question_id"]: e["question_type"]
                 for e in json.loads(Path(a.references).read_text(encoding="utf-8"))}

    for f in a.fichiers:
        entrees = lire(Path(f))
        ids = [e["question_id"] for e in entrees]
        if len(ids) != len(set(ids)):
            raise SystemExit(f"{f} : question_id en double.")
        modeles = sorted({e["autoeval_label"]["model"] for e in entrees})
        justes = sum(1 for e in entrees if e["autoeval_label"]["label"])
        print(f"{Path(f).name} : {justes}/{len(entrees)} = {100 * justes / len(entrees):.1f} %  (juge {', '.join(modeles)})")
        if types:
            par_type: dict[str, list[int]] = {}
            for e in entrees:
                v = 1 if e["autoeval_label"]["label"] else 0
                par_type.setdefault(types[e["question_id"]], []).append(v)
                if "_abs" in e["question_id"]:
                    par_type.setdefault("(abstention)", []).append(v)
            for t, v in sorted(par_type.items()):
                print(f"    {t:<28} {sum(v):>3}/{len(v):<3} {100 * sum(v) / len(v):5.1f} %")


if __name__ == "__main__":
    main()
