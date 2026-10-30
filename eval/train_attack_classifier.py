"""Train + compare attack detectors. Owner: Person 3.
Run: python -m eval.train_attack_classifier

Question: is a LEARNED classifier better than our similarity threshold at telling
attack messages from normal ones, on crypto/payment messages?

Data
  deepset/prompt-injections   546 train / 116 test, general prompt injection (1=attack, 0=normal)
  data/attacks.jsonl          34 crypto attacks (label 1)
  data/benign.jsonl           40 normal crypto/payment messages incl. tricky ones (label 0)

Methods, all scored on crypto messages they did NOT train on (5-fold cross-validation)
  A  similarity  current method: max cosine to known attacks >= threshold
  B  general     logistic regression trained on deepset only (does general injection knowledge transfer?)
  C  combined    logistic regression on deepset + crypto training folds
  D  crypto-only logistic regression on crypto training folds only

Output: metrics table, eval/classifier_results.json, models/attack_clf.joblib (method C, all data).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

from provenant.graph.attacks import load_attacks
from provenant.graph.embeddings import embed, embedder_name, threshold
from provenant.records import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "classifier_results.json"
MODEL = ROOT / "models" / "attack_clf.joblib"
SEED = 42


def load_crypto() -> tuple[list[str], np.ndarray]:
    attacks = [a["text"] for a in load_attacks()]
    benign = [json.loads(l)["text"] for l in (DATA_DIR / "benign.jsonl").read_text().splitlines() if l.strip()]
    return attacks + benign, np.array([1] * len(attacks) + [0] * len(benign))


def load_deepset():
    try:
        from datasets import load_dataset
        d = load_dataset("deepset/prompt-injections")
        return (list(d["train"]["text"]), np.array(d["train"]["label"]),
                list(d["test"]["text"]), np.array(d["test"]["label"]))
    except Exception as e:
        print(f"deepset not available ({e.__class__.__name__}); skipping methods B and C")
        return None


def metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum()); tn = int(((pred == 0) & (y == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0     # of what we flagged, how much was really an attack
    recall = tp / (tp + fn) if tp + fn else 0.0        # of real attacks, how many we caught
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0           # of normal messages, how many we wrongly flagged
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "false_alarm_rate": round(fpr, 3), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def new_clf() -> LogisticRegression:
    return LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)


def main() -> None:
    print(f"embedder: {embedder_name()}")
    texts, y = load_crypto()
    X = np.array(embed(texts))
    deep = load_deepset()
    if deep:
        Xd_tr, yd_tr = np.array(embed(deep[0])), deep[1]
        Xd_te, yd_te = np.array(embed(deep[2])), deep[3]

    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    preds = {k: np.zeros_like(y) for k in ["A_similarity", "C_combined", "D_crypto_only"]}

    for tr, te in folds.split(X, y):
        # A: similarity to known attacks in the TRAINING fold only (no peeking at the test message)
        attack_vecs = X[tr][y[tr] == 1]
        sims = (X[te] @ attack_vecs.T).max(axis=1)   # vectors are unit length, so dot = cosine
        preds["A_similarity"][te] = (sims >= threshold()).astype(int)

        preds["D_crypto_only"][te] = new_clf().fit(X[tr], y[tr]).predict(X[te])

        if deep:
            Xc = np.vstack([Xd_tr, X[tr]]); yc = np.concatenate([yd_tr, y[tr]])
            preds["C_combined"][te] = new_clf().fit(Xc, yc).predict(X[te])

    results = {"embedder": embedder_name(), "threshold": threshold(), "crypto_n": int(len(y)), "crypto": {}}
    results["crypto"]["A_similarity"] = metrics(y, preds["A_similarity"])
    results["crypto"]["D_crypto_only"] = metrics(y, preds["D_crypto_only"])
    if deep:
        general = new_clf().fit(Xd_tr, yd_tr)
        results["crypto"]["B_general"] = metrics(y, general.predict(X))
        results["crypto"]["C_combined"] = metrics(y, preds["C_combined"])
        results["deepset_test"] = {"B_general": metrics(yd_te, general.predict(Xd_te))}

    print(f"\nCrypto messages: {len(y)} ({int(y.sum())} attacks, {int((y == 0).sum())} normal), 5-fold CV")
    print(f"{'method':16s} {'precision':>9s} {'recall':>7s} {'f1':>6s} {'false alarms':>13s}")
    for name, m in sorted(results["crypto"].items()):
        print(f"{name:16s} {m['precision']:9.2f} {m['recall']:7.2f} {m['f1']:6.2f} {m['false_alarm_rate']:13.2f}")
    if deep:
        m = results["deepset_test"]["B_general"]
        print(f"\n(sanity) B_general on deepset's own test set: f1 {m['f1']:.2f}, recall {m['recall']:.2f}")

    RESULTS.write_text(json.dumps(results, indent=2))
    print(f"\nsaved {RESULTS.relative_to(ROOT)}")

    # Final model for the firewall: method C (or D if deepset missing), trained on ALL data.
    import joblib
    MODEL.parent.mkdir(exist_ok=True)
    Xf = np.vstack([Xd_tr, Xd_te, X]) if deep else X
    yf = np.concatenate([yd_tr, yd_te, y]) if deep else y
    joblib.dump({"model": new_clf().fit(Xf, yf), "embedder": embedder_name()}, MODEL)
    print(f"saved {MODEL.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
