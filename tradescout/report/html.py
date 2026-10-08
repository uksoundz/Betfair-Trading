from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import LEAGUE_NAMES
from ..scout import ScanResult

TEMPLATES = Path(__file__).parent / "templates"


def write_html(scan: ScanResult, out: Path | str, top: int = 40, min_score: float = 0.0) -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
    env.filters["pct"] = lambda v: "-" if v is None else f"{v:.0%}"
    env.filters["spct"] = lambda v: "-" if v is None else f"{v:+.1%}"
    env.filters["price"] = lambda v: "-" if v is None else f"{v:.2f}"
    tpl = env.get_template("scan.html")
    ideas = [i for i in scan.ideas if i.score >= min_score][:top]
    by_match = {}
    for i in scan.ideas:
        by_match.setdefault(i.fixture.label, []).append(i)
    html = tpl.render(scan=scan, ideas=ideas, by_match=by_match, league_names=LEAGUE_NAMES)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out
