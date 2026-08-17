#!/usr/bin/env python3
"""Extract and remove duplicate positive ECD dialogue samples.
The ECD files are tab-separated response-selection examples:

    label<TAB>utterance_1<TAB>...<TAB>response

Only label=1 rows form coherent conversations. Duplicate conversations are
merged while every original split and line number is retained for traceability.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


DEFAULT_INPUT_DIR = Path("data/dialogue_dataset/raw_samples")
DEFAULT_OUTPUT = Path(
    "data/dialogue_dataset/processed/positive_samples.json"
)
SPLITS = ("train", "valid", "test")


@dataclass(frozen=True)
class Source:
    split: str
    line: int

    def as_dict(self) -> dict[str, str | int]:
        return {"split": self.split, "line": self.line}


def parse_positive_rows(path: Path, split: str) -> list[tuple[tuple[str, ...], Source]]:
    """Return positive conversations and their source locations."""
    rows: list[tuple[tuple[str, ...], Source]] = []

    with path.open("r", encoding="utf-8") as source_file:
        for line_number, raw_line in enumerate(source_file, start=1):
            columns = raw_line.rstrip("\r\n").split("\t")
            if not columns or columns[0] != "1":
                continue
            if len(columns) < 3:
                raise ValueError(
                    f"{path}:{line_number}: positive row must contain a label, "
                    "conversation context, and response"
                )

            utterances = tuple(part.strip() for part in columns[1:])
            if any(not utterance for utterance in utterances):
                raise ValueError(
                    f"{path}:{line_number}: positive row contains an empty utterance"
                )
            rows.append((utterances, Source(split=split, line=line_number)))

    return rows


def extract_positive_samples(input_dir: Path) -> list[dict[str, object]]:
    """Read all splits, merge duplicate positive conversations, and assign IDs."""
    conversations: dict[tuple[str, ...], list[Source]] = {}

    for split in SPLITS:
        source_path = input_dir / f"{split}.tsv"
        if not source_path.is_file():
            raise FileNotFoundError(f"Missing source file: {source_path}")

        for utterances, source in parse_positive_rows(source_path, split):
            conversations.setdefault(utterances, []).append(source)

    samples: list[dict[str, object]] = []
    for index, (utterances, sources) in enumerate(conversations.items(), start=1):
        samples.append(
            {
                "source_dialogue_id": f"ECD_POS_{index:04d}",
                "sources": [source.as_dict() for source in sources],
                "label": 1,
                "utterances": list(utterances),
            }
        )

    return samples


def write_json(samples: Sequence[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(samples, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract label=1 ECD samples and merge duplicate conversations."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing train.tsv, valid.tsv, and test.tsv (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    samples = extract_positive_samples(args.input_dir)
    write_json(samples, args.output)

    source_count = sum(len(sample["sources"]) for sample in samples)
    duplicate_count = source_count - len(samples)
    print(f"Positive source rows: {source_count}")
    print(f"Unique conversations: {len(samples)}")
    print(f"Duplicate rows merged: {duplicate_count}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
