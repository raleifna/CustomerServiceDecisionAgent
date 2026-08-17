import re
from pathlib import Path
from typing import TypedDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "data" / "knowledge_base"

POLICY_FILES = {
    "refund": "refund_policy.md",
    "shipping": "shipping_policy.md",
    "vip": "vip_policy.md",
    "risk": "risk_policy.md",
}

POLICY_HEADING_PATTERN = re.compile(
    r"^##\s+([A-Z]+-\d{3})[：:]\s*(.+?)\s*$",
    re.MULTILINE,
)


class PolicyRule(TypedDict):
    policy_id: str
    title: str
    content: str
    source_file: str
    policy_type: str


def load_policy_document(policy_type: str) -> str:
    """Read one policy Markdown document by its policy type."""
    filename = POLICY_FILES.get(policy_type)

    if filename is None:
        valid_types = ", ".join(POLICY_FILES)
        raise ValueError(
            f"未知政策类型: {policy_type}; 可用类型: {valid_types}"
        )

    file_path = KNOWLEDGE_BASE_DIR / filename

    return file_path.read_text(encoding="utf-8")


def parse_policy_rules(
    document: str,
    policy_type: str,
) -> list[PolicyRule]:
    """Split a policy Markdown document into numbered policy rules."""
    filename = POLICY_FILES.get(policy_type)

    if filename is None:
        valid_types = ", ".join(POLICY_FILES)
        raise ValueError(
            f"未知政策类型: {policy_type}; 可用类型: {valid_types}"
        )

    matches = list(POLICY_HEADING_PATTERN.finditer(document))
    rules: list[PolicyRule] = []

    for index, match in enumerate(matches):
        section_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(document)
        )
        content = document[match.end():section_end].strip()

        rules.append(
            {
                "policy_id": match.group(1),
                "title": match.group(2).strip(),
                "content": content,
                "source_file": filename,
                "policy_type": policy_type,
            }
        )

    return rules


def get_all_policy_rules() -> list[PolicyRule]:
    """Load and parse every configured policy document."""
    rules: list[PolicyRule] = []
    seen_policy_ids: set[str] = set()

    for policy_type in POLICY_FILES:
        document = load_policy_document(policy_type)
        document_rules = parse_policy_rules(document, policy_type)

        for rule in document_rules:
            policy_id = rule["policy_id"]

            if policy_id in seen_policy_ids:
                raise ValueError(f"政策编号重复: {policy_id}")

            seen_policy_ids.add(policy_id)
            rules.append(rule)

    return rules


def get_policy_rule(policy_id: str) -> PolicyRule | None:
    """Find one policy rule by its stable policy ID."""
    normalized_policy_id = policy_id.strip().upper()

    for rule in get_all_policy_rules():
        if rule["policy_id"] == normalized_policy_id:
            return rule

    return None


def get_policy_rules_by_type(
    policy_type: str,
) -> list[PolicyRule]:
    """Return all numbered rules from one policy document."""
    document = load_policy_document(policy_type)
    return parse_policy_rules(document, policy_type)
