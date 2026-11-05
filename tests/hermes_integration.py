"""Integration check inside a real Hermes install: the plugin is discovered by Hermes's own plugin
manager, and the tool, the hook and the slash command are called through Hermes's own dispatch
paths (no LLM needed).

Run with the Python of a Hermes install, after `hermes plugins install ... --enable`:
    HERMES_HOME=<home> <hermes-venv>/bin/python tests/hermes_integration.py
"""
import json
import sys
from pathlib import Path

from hermes_cli.plugins import discover_plugins, get_plugin_command_handler, invoke_hook
from tools.registry import registry

HERE = Path(__file__).parent
reply = (HERE / "fixtures_mixed.md").read_text(encoding="utf-8")
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


discover_plugins(force=True)

# 1) tool, through the tool registry the agent loop uses
out = registry.dispatch("verify_citations", {"text": reply})
data = json.loads(out) if isinstance(out, str) else out
statuses = {r["label"]: r["status"] for r in data.get("results", [])}
check("tool registered and dispatchable", data.get("success") is True, str(out)[:200])
expected = {"[1]": "verified", "[2]": "verified", "[3]": "verified", "[4]": "verified", "[5]": "retracted",
            "[6]": "id_not_found", "[7]": "wrong_id", "[8]": "not_found", "[9]": "metadata_mismatch",
            "[10]": "retracted"}
for label, want in expected.items():
    check(f"tool verdict {label} = {want}", statuses.get(label) == want, statuses.get(label))

# 2) hook, through the same invoke_hook the turn finalizer uses
results = invoke_hook("transform_llm_output", response_text=reply, session_id="it", model="none",
                      platform="cli", turn_id="it-1")
rewrite = next((r for r in results if isinstance(r, str) and r), None)
check("hook appends a footer", bool(rewrite) and "**Citation Guard**" in rewrite)
check("hook keeps the original reply verbatim", bool(rewrite) and rewrite.startswith(reply.rstrip()))
clean = invoke_hook("transform_llm_output", response_text="The meeting moved to 3 pm.", session_id="it",
                    model="none", platform="cli", turn_id="it-2")
check("hook leaves replies without references alone", not any(isinstance(r, str) and r for r in clean))

# 3) slash command, through the plugin command table
handler = get_plugin_command_handler("citecheck")
check("/citecheck registered", handler is not None)
if handler:
    report = handler(str(HERE / "fixtures_mixed.md"))
    check("/citecheck produces a report", "Citation check: fixtures_mixed.md" in report, report[:120])
    check("/citecheck suggests the real paper for a wrong DOI", "arxiv.org/abs/2005.14165" in report)

print(f"\n{'OK' if not fails else 'FAILED'}: {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
