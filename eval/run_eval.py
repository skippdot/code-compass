"""Automated retrieval eval — recall@k / hit@k for our engine.

Turns "feels better" into a number so tuning (NAME_BOOST, mmr_lambda, the
stoplist, graph-expansion) can be measured instead of guessed, and across
repos so we don't overfit one. Each case lists `expect` path-substrings that a
good answer must surface; recall@k is the fraction found in the top-k results.

Run:  python eval/run_eval.py [k]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402,F401  -- loads .env
from retriever import Retriever  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def run(k: int = 8) -> None:
    cases = json.load(open(os.path.join(HERE, "eval_set.json")))
    retrievers: dict[str, Retriever] = {}
    by_repo: dict[str, list[float]] = {}
    hits_by_repo: dict[str, list[int]] = {}

    for case in cases:
        repo, query, expect = case["repo"], case["query"], case["expect"]
        if repo not in retrievers:
            retrievers[repo] = Retriever(repo)
        paths = [h["path"] for h in retrievers[repo].search(query, k=k)]
        found = [e for e in expect if any(e in p for p in paths)]
        recall = len(found) / len(expect)
        by_repo.setdefault(repo, []).append(recall)
        hits_by_repo.setdefault(repo, []).append(1 if found else 0)
        mark = "OK " if found else "MISS"
        miss = "" if len(found) == len(expect) else f"  (missing {set(expect) - set(found)})"
        print(f"  [{mark}] {repo:<10} r@{k}={recall:.2f}  {query[:54]}{miss}")

    print("\n--- per repo ---")
    all_r, all_h = [], []
    for repo in by_repo:
        r = by_repo[repo]
        h = hits_by_repo[repo]
        all_r += r
        all_h += h
        print(f"  {repo:<10} recall@{k}={sum(r)/len(r):.2f}  "
              f"hit@{k}={sum(h)/len(h):.2f}  (n={len(r)})")
    print(f"\nOVERALL  recall@{k}={sum(all_r)/len(all_r):.3f}  "
          f"hit@{k}={sum(all_h)/len(all_h):.3f}  (n={len(all_r)})")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
