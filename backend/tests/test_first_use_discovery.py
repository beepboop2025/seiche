from html.parser import HTMLParser
from pathlib import Path
import ast
import subprocess

ROOT = Path(__file__).resolve().parents[2]

class Snippets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.active = False
    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.active = True
            self.blocks.append("")
    def handle_endtag(self, tag):
        if tag == "pre":
            self.active = False
    def handle_data(self, text):
        if self.active:
            self.blocks[-1] += text


def test_quant_setup_is_one_copyable_shell_block_with_a_python_boundary():
    snippets = Snippets()
    snippets.feed((ROOT / "frontend/public/agents/quant/index.html").read_text())
    command = next(block for block in snippets.blocks if "pip install" in block)
    checked = subprocess.run(["/bin/sh", "-n"], input=command, text=True,
                             capture_output=True, timeout=5)
    assert checked.returncode == 0, checked.stderr
    assert "@agent-v1.0.0" in command
    python_body = command.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    parsed = ast.parse(python_body)
    assert any(isinstance(node, ast.With) for node in parsed.body)
    assert "framework_tools('langchain', evidence)" in python_body
