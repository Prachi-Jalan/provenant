"""Evaluation harness. Owner: Person 3.
Run: python -m eval.run_eval

For each line in eval/cases.jsonl (format in CONTRACTS.md):
  make_policy -> build ProposedTx from "proposed" (or run the agent) -> firewall.check
Write one row per case to eval/results.jsonl, then print:
  attack success rate, task completion rate, auto-repair rate, false block rate.
Compare setups: no defense / LLM judge only / + provenance / full system.
"""


def main() -> None:
    raise NotImplementedError("Person 3")


if __name__ == "__main__":
    main()
