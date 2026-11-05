"""Human-readable output: a full report (slash command) and a short footer (reply guard)."""

from __future__ import annotations

ICON = {
    "id_not_found": "✗", "wrong_id": "✗", "retracted": "✗", "not_found": "?",
    "metadata_mismatch": "⚠", "concern": "⚠", "unchecked": "·", "verified": "✓",
}
LABEL = {
    "id_not_found": "identifier does not exist",
    "wrong_id": "identifier points to a different work",
    "retracted": "retracted",
    "not_found": "not found in Crossref, PubMed or arXiv",
    "metadata_mismatch": "details differ from the registry",
    "concern": "expression of concern",
    "unchecked": "could not be checked",
    "verified": "verified",
}


def _registry_line(rec: dict) -> str:
    if not rec:
        return ""
    fam = rec.get("first_author_family")
    who = (fam + (" et al." if (rec.get("n_authors") or 0) > 1 else "")) if fam else ""
    bits = [b for b in (who, str(rec.get("year") or ""), f"“{rec.get('title')}”" if rec.get("title") else "",
                        rec.get("journal") or "") if b]
    link = rec.get("url")
    return ", ".join(bits) + (f" — {link}" if link else "")


def _item(r: dict) -> str:
    head = f"{ICON[r['status']]} {r.get('label') or ''} {LABEL[r['status']]}".replace("  ", " ")
    lines = [head]
    for issue in r.get("issues") or []:
        lines.append(f"    - {issue}")
    if r["status"] in ("wrong_id",) and r.get("registry"):
        lines.append(f"    - that identifier is: {_registry_line(r['registry'])}")
    if r.get("suggestion"):
        lines.append(f"    - closest real record: {_registry_line(r['suggestion'])}")
    return "\n".join(lines)


def footer(summary: dict) -> str:
    """Appended to an agent reply when at least one citation has a problem."""
    probs = [r for r in summary["results"] if r["status"] not in ("verified", "unchecked")]
    ok = summary["counts"].get("verified", 0)
    unk = summary["counts"].get("unchecked", 0)
    out = ["", "---",
           f"**Citation Guard** checked {summary['checked']} reference(s) against Crossref, PubMed "
           f"and arXiv in {summary['seconds']} s: {len(probs)} problem(s), {ok} verified"
           + (f", {unk} not checked" if unk else "") + "."]
    for r in probs:
        cited = r["cited"] if len(r["cited"]) <= 110 else r["cited"][:107] + "..."
        out.append(f"- {ICON[r['status']]} **{LABEL[r['status']]}**: {cited}")
        for issue in (r.get("issues") or [])[:2]:
            out.append(f"  - {issue}")
        if r.get("suggestion"):
            out.append(f"  - closest real record: {_registry_line(r['suggestion'])}")
    out.append("_Do not rely on flagged references until you have checked them yourself._")
    return "\n".join(out)


def report(summary: dict, title: str = "Citation check") -> str:
    """Full report for the /citecheck command."""
    c = summary["counts"]
    out = [f"## {title}",
           f"{summary['checked']} reference(s) checked in {summary['seconds']} s · "
           + " · ".join(f"{ICON[k]} {LABEL[k]}: {v}" for k, v in c.items()), ""]
    for r in sorted(summary["results"],
                    key=lambda r: ["id_not_found", "wrong_id", "retracted", "not_found", "metadata_mismatch",
                                   "concern", "unchecked", "verified"].index(r["status"])):
        out.append(_item(r))
        out.append(f"    - cited: {r['cited']}")
        if r["status"] == "verified" and r.get("registry"):
            out.append(f"    - registry: {_registry_line(r['registry'])}")
    out += ["", "Sources: doi.org, Crossref (incl. Retraction Watch notices), PubMed E-utilities, arXiv. "
                "“Not found” can also mean the work is not indexed (books, local journals, reports)."]
    return "\n".join(out)
