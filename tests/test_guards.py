import ast
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rushes.cli import app

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "rushes"

REMOTE_AI = ("openai", "anthropic", "google.generativeai", "google.genai", "mistralai")
REMOTE_AI += ("cohere", "groq", "replicate")
REMOTE_AI_URLS = ("api.openai.com", "api.anthropic.com")


def _modules(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
    return found


def _is(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def test_r20_network_is_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="R20"):
        socket.create_connection(("example.org", 80))


def test_r20_raw_socket_connect_is_blocked_in_tests() -> None:
    sock = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="R20"):
            sock.connect(("example.org", 80))
    finally:
        sock.close()


def test_r20_dns_connect_ex_and_udp_are_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="R20"):
        socket.getaddrinfo("example.org", 80)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(RuntimeError, match="R20"):
            sock.connect_ex(("example.org", 80))
        with pytest.raises(RuntimeError, match="R20"):
            sock.sendto(b"x", ("example.org", 80))
    finally:
        sock.close()


def test_r39_no_remote_ai_client_imported() -> None:
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for module in _modules(ast.parse(source)):
            assert not any(_is(module, banned) for banned in REMOTE_AI), (path.name, module)
        for url in REMOTE_AI_URLS:
            assert url not in source, (path.name, url)


def test_only_ui_imports_rich() -> None:
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if path.name != "ui.py" or path.parent != SRC
        for module in _modules(ast.parse(path.read_text(encoding="utf-8")))
        if _is(module, "rich")
    ]
    assert offenders == []


def test_r40_vendor_dir_requires_vendor_md() -> None:
    vendor = ROOT / "vendor"
    if vendor.exists():
        assert (vendor / "VENDOR.md").is_file()


def test_r41_vendor_check_absent_while_vendor_empty() -> None:
    if (ROOT / "vendor").exists():
        pytest.skip("vendor/ exists: `vendor check` is expected then")
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "vendor" not in result.output
