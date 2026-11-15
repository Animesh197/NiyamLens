"""Minimal synthetic HTML shaped like the RBI Master Direction pages (observed 14 Nov 2026).

These are hand-written test fixtures, not copies of RBI pages.
"""

FAKE_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
BOT_CHALLENGE_HTML = (
    b"<!DOCTYPE html><html><body>Please enable JavaScript to view the page content. "
    b"This question is for testing whether you are a human visitor and to prevent "
    b"automated spam submission.</body></html>"
)


def direction_page(
    *,
    title: str,
    pdf_url: str,
    issue_date: str = "November 28, 2025",
    identifiers: tuple[str, ...] = ("RBI/DOR/2025-26/351", "DOR.CRE.REC.270/07-03-008/2025-26"),
    annex_urls: tuple[str, ...] = (),
    extra_main_pdf_urls: tuple[str, ...] = (),
    injected_text: str = "",
) -> str:
    anchors = "".join(
        f'<a id="APDF_{i}" href="{url}"><img alt="PDF"></a>'
        for i, url in enumerate((pdf_url, *extra_main_pdf_urls))
    )
    annexes = "".join(
        f'<tr><td><a href="{u}" class="links">Annex</a></td></tr>' for u in annex_urls
    )
    ids = "<br>".join(identifiers)
    return f"""<!doctype html><html><head><title>Master Directions</title></head><body>
<a href="https://rbidocs.rbi.org.in/rdocs/content/pdfs/Accessibility20012026.pdf">site pdf</a>
<div role="main"><table class="tablebg" width="100%">
<tr><td class="tableheader">{anchors} (<span>407 kb</span>)</td></tr>
<tr><td class="tableheader"><b>{title}</b></td></tr>
<tr class="tablecontent2"><td><table class="td"><tr><td>
<p>{ids}</p>
<p align="right">{issue_date}</p>
<p align="right"><a href="NotificationPreVersion.aspx?id=1">Previous Versions</a></p>
<p>{injected_text}</p>
<table class="tablebg">{annexes}</table>
</td></tr></table></td></tr></table></div></body></html>"""


def previous_versions_page(entries: tuple[tuple[str, str], ...]) -> str:
    """entries: (histid, label) pairs. Empty -> RBI's 'no previous version' message."""
    if not entries:
        body = "No Master Directions - Privious Version Found."
    else:
        body = "".join(
            f'<a href="NotificationPreVersion.aspx?id=12953&Histid={hid}">{label}</a><br>'
            for hid, label in entries
        )
    return (
        "<!doctype html><html><body><h1>Master Directions - Previous Versions</h1>"
        f"<div>{body}</div></body></html>"
    )
