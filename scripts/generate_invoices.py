"""CLI: generate a deterministic synthetic invoice corpus.

    python -m scripts.generate_invoices --count 300 --seed 42 --out corpus/
"""

import argparse

from scripts.seed_reference import BUDGETS  # the one allowed dependency (M2-A §7)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="corpus")
    args = parser.parse_args()
    raise SystemExit(f"not implemented yet (count={args.count}, budgets={len(BUDGETS)})")


if __name__ == "__main__":
    main()
