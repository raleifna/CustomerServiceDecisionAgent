#!/usr/bin/env python3
"""Screen positive ECD dialogues using configurable keyword categories."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path(
    "data/dialogue_dataset/processed/positive_samples.json"
)
DEFAULT_KEYWORDS = Path(
    "data/dialogue_dataset/config/screening_keywords.json"
)
DEFAULT_OUTPUT = Path(
    "data/dialogue_dataset/processed/candidate_samples.json"
)
DEFAULT_REVIEWS = Path(
    "data/dialogue_dataset/reviews/manual_reviews.json"
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as input_file:
        return json.load(input_file)


def validate_keywords(raw_keywords: Any) -> dict[str, list[str]]:
    if not isinstance(raw_keywords, dict) or not raw_keywords:
        raise ValueError("Keyword config must be a non-empty JSON object")

    normalized: dict[str, list[str]] = {}
    keyword_owner: dict[str, str] = {}

    for category, words in raw_keywords.items():
        if not isinstance(category, str) or not category.strip():
            raise ValueError("Every keyword category must be a non-empty string")
        if not isinstance(words, list) or not words:
            raise ValueError(f"Category {category!r} must contain a non-empty list")

        unique_words: list[str] = []
        for word in words:
            if not isinstance(word, str) or not word.strip():
                raise ValueError(
                    f"Category {category!r} contains an invalid keyword: {word!r}"
                )
            word = word.strip()
            if word in unique_words:
                continue
            if word in keyword_owner and keyword_owner[word] != category:
                raise ValueError(
                    f"Keyword {word!r} appears in both {keyword_owner[word]!r} "
                    f"and {category!r}"
                )
            keyword_owner[word] = category
            unique_words.append(word)

        normalized[category.strip()] = unique_words

    return normalized


def compact_for_matching(text: str) -> str:
    """Remove whitespace because ECD Chinese text is word-segmented with spaces."""
    return "".join(text.split())


def find_matches(
    utterances: list[str], keyword_groups: dict[str, list[str]]
) -> list[dict[str, object]]:
    searchable_text = compact_for_matching(" ".join(utterances))
    matches: list[dict[str, object]] = []

    for category, keywords in keyword_groups.items():
        matched_keywords = [
            keyword
            for keyword in keywords
            if compact_for_matching(keyword) in searchable_text
        ]
        if matched_keywords:
            matches.append(
                {
                    "category": category,
                    "keywords": matched_keywords,
                }
            )

    return matches


def screen_samples(
    samples: Any,
    keyword_groups: dict[str, list[str]],
    reviews: dict[str, dict[str, str | None]],
) -> list[dict[str, object]]:
    if not isinstance(samples, list):
        raise ValueError("Positive samples file must contain a JSON array")

    candidates: list[dict[str, object]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("Every positive sample must be a JSON object")
        utterances = sample.get("utterances")
        if not isinstance(utterances, list) or not all(
            isinstance(utterance, str) for utterance in utterances
        ):
            raise ValueError(
                f"Sample {sample.get('source_dialogue_id')} has invalid utterances"
            )

        keyword_matches = find_matches(utterances, keyword_groups)
        if not keyword_matches:
            continue

        source_dialogue_id = sample.get("source_dialogue_id")
        review = reviews.get(str(source_dialogue_id), {})
        candidates.append(
            {
                "source_dialogue_id": source_dialogue_id,
                "sources": sample.get("sources", []),
                "utterances": utterances,
                "keyword_matches": keyword_matches,
                "review_status": review.get("review_status", "Pending"),
                "review_notes": review.get("review_notes"),
            }
        )

    return candidates


def write_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def print_summary(candidates: list[dict[str, object]]) -> None:
    category_counts: Counter[str] = Counter()
    keyword_counts: Counter[str] = Counter()

    for candidate in candidates:
        for match in candidate["keyword_matches"]:  # type: ignore[index]
            category_counts[match["category"]] += 1
            keyword_counts.update(match["keywords"])

    print(f"Candidate dialogues: {len(candidates)}")
    print("Category matches (a dialogue may match multiple categories):")
    for category, count in category_counts.most_common():
        print(f"  {category}: {count}")
    print("Keyword matches:")
    for keyword, count in keyword_counts.most_common():
        print(f"  {keyword}: {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select ECD positive dialogues that match after-sales keywords."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--reviews",
        type=Path,
        default=DEFAULT_REVIEWS,
        help="Optional manual review mapping preserved across screening runs",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    keyword_groups = validate_keywords(load_json(args.keywords))
    reviews = load_json(args.reviews) if args.reviews.is_file() else {}
    if not isinstance(reviews, dict):
        raise ValueError("Manual reviews file must contain a JSON object")
    candidates = screen_samples(load_json(args.input), keyword_groups, reviews)
    write_json(candidates, args.output)
    print_summary(candidates)
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
