"""Juge officiel LongMemEval (GPT-4o), sans dépendance externe.

Les consignes du juge ne sont pas recopiées : la fonction ``get_anscheck_prompt``
est extraite telle quelle (AST) du fichier officiel ``evaluate_qa.py`` posé à
côté de ce script (dépôt xiaowu0162/LongMemEval, licence MIT, SHA-256 vérifié),
puis compilée seule. Le reste reproduit evaluate_qa.py : modèle
gpt-4o-2024-08-06, un message utilisateur, n=1, température 0, max_tokens 10,
label = 'yes' dans la réponse mise en minuscules, abstention = '_abs' dans
question_id.

Usage :
  python3 -I juge_officiel.py HYPOTHESES REFERENCES SORTIE [--cle FICHIER] [--travailleurs 4]

  HYPOTHESES  une ligne JSON par question : {"question_id": ..., "hypothesis": ...}
  REFERENCES  longmemeval_s_cleaned.json (jeu officiel, à télécharger chez ses auteurs)
  SORTIE      fichier de résultats au format officiel (autoeval_label ajouté)

La clé vient de --cle (fichier lisible par le seul propriétaire) ou de
OPENAI_API_KEY. Elle n'est jamais affichée ni écrite. Une sortie existante est
reprise : les questions déjà jugées ne sont pas renvoyées.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OFFICIEL = Path(__file__).resolve().parent / "evaluate_qa.py"
OFFICIEL_SHA256 = "ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251"
MODELE = "gpt-4o-2024-08-06"
URL = "https://api.openai.com/v1/chat/completions"


def charger_consignes():
    source = OFFICIEL.read_bytes()
    empreinte = hashlib.sha256(source).hexdigest()
    if empreinte != OFFICIEL_SHA256:
        sys.exit(f"evaluate_qa.py ne correspond pas à la version officielle (SHA-256 {empreinte}) : arrêt.")
    arbre = ast.parse(source.decode("utf-8"))
    fonctions = [n for n in arbre.body
                 if isinstance(n, ast.FunctionDef) and n.name == "get_anscheck_prompt"]
    if len(fonctions) != 1:
        sys.exit("get_anscheck_prompt introuvable dans evaluate_qa.py : arrêt.")
    module = ast.Module(body=fonctions, type_ignores=[])
    espace: dict = {}
    exec(compile(module, str(OFFICIEL), "exec"),
         {"__builtins__": {"NotImplementedError": NotImplementedError}}, espace)
    return espace["get_anscheck_prompt"]


def lire_cle(chemin: str | None) -> str:
    if chemin:
        p = Path(chemin).expanduser()
        mode = p.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            sys.exit(f"{p} est lisible par d'autres que vous : chmod 600 {p}")
        cle = p.read_text(encoding="utf-8").strip()
    else:
        cle = os.environ.get("OPENAI_API_KEY", "").strip()
    if not cle:
        sys.exit("Aucune clé : --cle FICHIER ou OPENAI_API_KEY.")
    return cle


def lire_jsonl_ou_json(chemin: Path):
    texte = chemin.read_text(encoding="utf-8")
    try:
        return [json.loads(l) for l in texte.split("\n") if l.strip()]
    except json.JSONDecodeError:
        return json.loads(texte)


def appeler(cle: str, prompt: str) -> str:
    corps = json.dumps({
        "model": MODELE,
        "messages": [{"role": "user", "content": prompt}],
        "n": 1,
        "temperature": 0,
        "max_tokens": 10,
    }).encode("utf-8")
    attente = 1.0
    for essai in range(8):
        requete = urllib.request.Request(URL, data=corps, method="POST", headers={
            "Authorization": f"Bearer {cle}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(requete, timeout=60) as r:
                rep = json.loads(r.read().decode("utf-8"))
            return rep["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and essai < 7:
                time.sleep(attente)
                attente = min(attente * 2, 60)
                continue
            raise SystemExit(f"OpenAI HTTP {e.code} : arrêt (la clé n'est pas affichée).")
        except (urllib.error.URLError, TimeoutError):
            if essai < 7:
                time.sleep(attente)
                attente = min(attente * 2, 60)
                continue
            raise
    raise SystemExit("OpenAI injoignable après 8 essais.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Juge officiel LongMemEval (GPT-4o).")
    ap.add_argument("hypotheses")
    ap.add_argument("references")
    ap.add_argument("sortie")
    ap.add_argument("--cle")
    ap.add_argument("--travailleurs", type=int, default=4)
    a = ap.parse_args()

    get_anscheck_prompt = charger_consignes()
    cle = lire_cle(a.cle)
    hypotheses = lire_jsonl_ou_json(Path(a.hypotheses))
    references = lire_jsonl_ou_json(Path(a.references))
    qdata = {e["question_id"]: e for e in references}

    sortie = Path(a.sortie)
    deja = {}
    if sortie.exists():
        for l in sortie.read_text(encoding="utf-8").split("\n"):
            if l.strip():
                e = json.loads(l)
                deja[e["question_id"]] = e
    a_juger = [h for h in hypotheses if h["question_id"] in qdata and h["question_id"] not in deja]
    ignorees = [h["question_id"] for h in hypotheses if h["question_id"] not in qdata]
    for q in ignorees:
        print(f"Warning: skipping {q} as it is not in reference data.")
    print(f"{len(deja)} déjà jugées, {len(a_juger)} à juger, modèle {MODELE}", flush=True)

    verrou = threading.Lock()

    def juger(entree):
        d = qdata[entree["question_id"]]
        prompt = get_anscheck_prompt(d["question_type"], d["question"], d["answer"],
                                     entree["hypothesis"], abstention="_abs" in entree["question_id"])
        texte = appeler(cle, prompt)
        sortie_entree = {"question_id": entree["question_id"], "hypothesis": entree["hypothesis"],
                         "autoeval_label": {"model": MODELE, "label": "yes" in texte.lower()}}
        with verrou:
            with sortie.open("a", encoding="utf-8") as f:
                f.write(json.dumps(sortie_entree) + "\n")
            deja[entree["question_id"]] = sortie_entree
            if len(deja) % 50 == 0:
                print(f"{len(deja)} jugées", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, a.travailleurs)) as pool:
        list(pool.map(juger, a_juger))

    ids = [h["question_id"] for h in hypotheses if h["question_id"] in qdata]
    labels = [1 if deja[q]["autoeval_label"]["label"] else 0 for q in ids]
    print("Accuracy:", round(sum(labels) / len(labels), 4), f"({sum(labels)}/{len(labels)})")
    par_type: dict[str, list[int]] = {}
    for q, v in zip(ids, labels):
        par_type.setdefault(qdata[q]["question_type"], []).append(v)
    for t, v in sorted(par_type.items()):
        print(f"\t{t}: {round(sum(v) / len(v), 4)} ({len(v)})")
    print("Saved to", sortie)


if __name__ == "__main__":
    main()
