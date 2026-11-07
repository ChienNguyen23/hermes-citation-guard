"""citation-guard: check that cited papers exist, match what was cited, and are not retracted.

Surfaces
  * tool  ``verify_citations``      the model checks a bibliography before presenting it
  * hook  ``transform_llm_output``  every final reply that contains references gets checked;
                                    problems are appended as a short footer (never rewritten)
  * slash ``/citecheck <file|text>`` a full report for a manuscript (.md .txt .bib .docx)

Deterministic and LLM-free: identifiers are resolved at doi.org / PubMed / arXiv, titles are
matched in Crossref and PubMed, retractions come from Crossref update notices (which include
the Retraction Watch database). Network failures are reported as "could not be checked",
never as "fake".
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Optional

from . import extract, render, sources, verify

logger = logging.getLogger(__name__)

try:  # documented helpers used by the official example plugins
    from tools.registry import tool_error, tool_result
except Exception:  # pragma: no cover - standalone use / tests
    def tool_result(data=None, **kwargs):
        return json.dumps(data if data is not None else kwargs, ensure_ascii=False)

    def tool_error(message, **extra):
        return json.dumps({"success": False, "error": message, **extra}, ensure_ascii=False)

PLUGIN_ID = "citation-guard"
MAX_TEXT_CHARS = 200_000
MAX_FILE_BYTES = 10 * 1024 * 1024
_FOOTER_MARK = "**Citation Guard**"

_ctx: Any = None
_client: Optional[sources.Client] = None


def _cfg(key: str, default):
    if _ctx is None:
        return default
    try:
        value = _ctx.get_config(key, default=default)
    except Exception:
        return default
    return default if value is None else value


def _get_client() -> sources.Client:
    global _client
    mailto = str(_cfg("contact_email", "") or "")
    timeout = float(_cfg("request_timeout_s", 8) or 8)
    ncbi_key = str(_cfg("ncbi_api_key", "") or "")
    if _client is None or _client.mailto != mailto or _client.timeout != timeout or _client.ncbi_api_key != ncbi_key:
        cache_path = None
        try:
            from plugins.plugin_storage import plugin_data_dir
            cache_path = Path(plugin_data_dir(PLUGIN_ID)) / "registry_cache.json"
        except Exception:
            cache_path = None
        _client = sources.Client(sources.Cache(cache_path), mailto=mailto, timeout=timeout, ncbi_api_key=ncbi_key)
    return _client


def _citations_from(text: str, max_items: int) -> list[dict]:
    if re.search(r"@\w+\s*\{", text):
        bib = extract.parse_bibtex(text, max_items=max_items)
        if bib:
            return bib
    return extract.extract_citations(text, max_items=max_items)


# ---------------------------------------------------------------- tool
VERIFY_SCHEMA = {
    "description": (
        "Check references against scholarly registries (doi.org, Crossref, PubMed, arXiv). "
        "For each reference it reports whether the DOI/PMID/arXiv id exists, whether it points to "
        "the cited title, whether year and first author match, and whether the work was retracted. "
        "Call this BEFORE presenting any list of papers, citations or a bibliography to the user, "
        "and when the user asks whether references are real. Input is plain text: a reference list, "
        "a draft with a References section, or BibTeX. Deterministic; no LLM involved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text containing the references (one per line or a References section), "
                               "or BibTeX entries. Up to 200,000 characters.",
            },
            "check_retractions": {
                "type": "boolean",
                "description": "Also look up retraction / withdrawal notices. Defaults to true.",
            },
            "max_items": {
                "type": "integer",
                "description": "Maximum number of references to check (1-200). Defaults to 40.",
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}


def _handle_verify(args: dict, **kwargs: Any) -> str:
    del kwargs
    try:
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            return tool_error("text must be a non-empty string")
        if len(text) > MAX_TEXT_CHARS:
            return tool_error("text exceeds the 200,000 character limit", max_chars=MAX_TEXT_CHARS)
        try:
            max_items = max(1, min(200, int(args.get("max_items") or _cfg("max_items", 40))))
        except (TypeError, ValueError):
            max_items = 40
        cits = _citations_from(text, max_items)
        if not cits:
            return tool_result({"success": True, "checked": 0,
                                "note": "No checkable references found (need a DOI, PMID, arXiv id, "
                                        "or a full entry with authors, year and title)."})
        summary = verify.check_all(cits, _get_client(),
                                   check_retraction=bool(args.get("check_retractions", True)))
        summary["success"] = True
        summary["legend"] = render.LABEL
        return tool_result(summary)
    except Exception as e:  # tools must never raise
        logger.warning("citation-guard: verify_citations failed", exc_info=True)
        return tool_error(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- hook
def _on_llm_output(response_text: str = "", **_: Any) -> Optional[str]:
    """Append a short warning footer when the reply cites references that fail the check.

    Returns None (leave the reply untouched) when there is nothing to check, everything checks
    out, or nothing could be checked. The original text is never altered, only appended to.
    """
    try:
        if not response_text or not bool(_cfg("guard_replies", True)) or _FOOTER_MARK in response_text:
            return None
        cits = extract.extract_citations(response_text, max_items=int(_cfg("max_items", 40) or 40))
        if not cits:
            return None
        summary = verify.check_all(cits, _get_client(), check_retraction=True, suggest=False,
                                   time_budget=float(_cfg("guard_timeout_s", 15) or 15))
        if summary["problems"]:
            return response_text.rstrip() + "\n" + render.footer(summary)
        verified = summary["counts"].get("verified", 0)
        if verified and bool(_cfg("announce_clean", False)):
            return (response_text.rstrip() + f"\n\n{_FOOTER_MARK}: all {verified} reference(s) verified "
                    f"against Crossref/PubMed/arXiv.")
        return None
    except Exception:
        logger.warning("citation-guard: reply check failed", exc_info=True)
        return None


# ---------------------------------------------------------------- slash command
def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(a, b)
    return text


def _resolve_user_path(arg: str) -> Path:
    """A relative path is the caller's: when a gateway serves the chat, the command runs in the
    gateway process, whose own cwd is not the directory the user typed the command in."""
    p = Path(os.path.expanduser(arg))
    if p.is_absolute():
        return p
    base = ""
    try:
        if _ctx is not None and hasattr(_ctx, "session_cwd"):
            base = _ctx.session_cwd() or ""
    except Exception:  # older Hermes or no session: fall back to this process's cwd
        base = ""
    return Path(base or os.getcwd()) / p


def _handle_command(raw_args: str = "", **_: Any) -> str:
    arg = (raw_args or "").strip()
    if not arg:
        return ("Usage: /citecheck <path to .md/.txt/.bib/.docx>  or  /citecheck <pasted references>\n"
                "Checks every reference against doi.org, Crossref, PubMed and arXiv.")
    title = "Citation check"
    text = arg
    candidate = _resolve_user_path(arg.strip("'\""))
    try:
        is_file = len(arg) < 1024 and candidate.is_file()
    except OSError:
        is_file = False
    if is_file:
        if candidate.stat().st_size > MAX_FILE_BYTES:
            return f"{candidate.name} is larger than 10 MB; paste the reference list instead."
        try:
            text = _read_docx(candidate) if candidate.suffix.lower() == ".docx" \
                else candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile, KeyError) as e:
            return f"Could not read {candidate.name}: {e}"
        title = f"Citation check: {candidate.name}"
    elif re.fullmatch(r"\S+\.(md|txt|bib|docx)", arg.strip("'\""), re.I):
        return (f"File not found: {candidate}\n"
                "Relative paths are resolved against the session's working directory; "
                "pass an absolute path (or ~/...) to be sure.")
    cits = _citations_from(text, int(_cfg("max_items", 40) or 40) if not is_file else 200)
    if not cits:
        return "No checkable references found (need a DOI, PMID, arXiv id, or a full entry with authors, year and title)."
    return render.report(verify.check_all(cits, _get_client()), title=title)


def register(ctx: Any) -> None:
    global _ctx
    _ctx = ctx
    ctx.register_tool(
        name="verify_citations",
        toolset="citation_guard",
        schema=VERIFY_SCHEMA,
        handler=_handle_verify,
        description="Verify that cited papers exist, match the citation, and are not retracted.",
    )
    ctx.register_hook("transform_llm_output", _on_llm_output)
    ctx.register_command("citecheck", _handle_command,
                         description="Check every reference in a file or pasted text against doi.org, "
                                     "Crossref, PubMed and arXiv",
                         args_hint="<file.md|.txt|.bib|.docx or pasted references>")
    logger.debug("citation-guard: registered verify_citations, transform_llm_output, /citecheck")
