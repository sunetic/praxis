from pathlib import Path

from tools.semantic_guard_scan import scan_file


def test_semantic_guard_scan_flags_keyword_membership_on_user_input(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_text(
        """
def route(user_input: str) -> bool:
    if "save agent" in user_input.lower():
        return True
    return False
""",
        encoding="utf-8",
    )
    findings = scan_file(target)
    assert findings
    assert findings[0].code == "SG001"


def test_semantic_guard_scan_flags_startswith_on_user_input(tmp_path: Path) -> None:
    target = tmp_path / "bad2.py"
    target.write_text(
        """
def route(incoming_content: str) -> bool:
    if incoming_content.startswith("/save"):
        return True
    return False
""",
        encoding="utf-8",
    )
    findings = scan_file(target)
    assert findings
    assert findings[0].code == "SG002"


def test_semantic_guard_scan_ignores_non_user_text_checks(tmp_path: Path) -> None:
    target = tmp_path / "ok.py"
    target.write_text(
        """
def parse_line(line: str) -> bool:
    if line.startswith("data:"):
        return True
    return False
""",
        encoding="utf-8",
    )
    findings = scan_file(target)
    assert findings == []


def test_semantic_guard_scan_flags_alias_from_user_input(tmp_path: Path) -> None:
    target = tmp_path / "alias.py"
    target.write_text(
        """
def route(raw_user_input: str) -> bool:
    normalized = raw_user_input.strip().lower()
    if "save agent" in normalized:
        return True
    return False
""",
        encoding="utf-8",
    )
    findings = scan_file(target)
    assert findings
    assert findings[0].code == "SG001"


def test_semantic_guard_scan_flags_renamed_message_parameter(tmp_path: Path) -> None:
    target = tmp_path / "renamed.py"
    target.write_text(
        """
def route(user_message_text: str) -> bool:
    cleaned = user_message_text.strip()
    if cleaned.startswith("/save"):
        return True
    return False
""",
        encoding="utf-8",
    )
    findings = scan_file(target)
    assert findings
    assert findings[0].code == "SG002"


def test_semantic_guard_scan_flags_dict_get_key_hint(tmp_path: Path) -> None:
    target = tmp_path / "dict_get.py"
    target.write_text(
        """
import re

def route(payload: dict[str, str]) -> bool:
    content = str(payload.get("incoming_content", "")).strip()
    if re.search("save\\\\s+agent", content):
        return True
    return False
""",
        encoding="utf-8",
    )
    findings = scan_file(target)
    assert findings
    assert findings[0].code == "SG003"


def test_semantic_guard_scan_ignores_backend_policy_checks(tmp_path: Path) -> None:
    target = tmp_path / "policy.py"
    target.write_text(
        """
def derive(capability_key: str, action: str) -> bool:
    allowed_tools = {"object_crud", "object_operate", "execute_sql", "explain_sql"}
    if capability_key == "object.operate":
        return True
    if action in {"create", "update", "delete"}:
        return True
    return capability_key in allowed_tools
""",
        encoding="utf-8",
    )
    findings = scan_file(target)
    assert findings == []
