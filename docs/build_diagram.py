#!/usr/bin/env python3
"""Build docs/workflow.png -- the architecture diagram in the README.

Run:  python3 docs/build_diagram.py    # writes docs/diagram.html
      open docs/diagram.html           # then screenshot the SVG

The edge list below mirrors src/graph/build_graph.py. If you add or move a node
there, change it here too -- that is the one thing this script cannot check for
you.

Every node carries three layers of label, because the diagram has two
audiences: the code identifier (monospace, for someone reading build_graph.py),
a plain-English line (for someone who has never seen the repo), and a tool tag
naming what actually does the work.

Icons: Lucide (ISC, stroked) and Simple Icons (CC0, filled), inlined at build
time so the output has no network dependency.
"""
import pathlib
import re

HERE = pathlib.Path(__file__).parent
ICONS = HERE / "icons"

BLUE, GREEN, AMBER, PURPLE, SLATE = "#3b82f6", "#22c55e", "#f59e0b", "#a855f7", "#475569"
DOCKER, LANGCHAIN, PG, GH, MODEL = "#2496ED", "#1C3C3C", "#4169E1", "#181717", "#7c3aed"


def _inner(path: pathlib.Path) -> str:
    raw = path.read_text()
    s = re.sub(r"^.*?<svg[^>]*>", "", raw, flags=re.S).replace("</svg>", "")
    return re.sub(r"<title>.*?</title>", "", s, flags=re.S).strip()


def icon(name, cx, cy, color, scale=1.7):
    """Lucide icon — stroked, centred on (cx, cy)."""
    off = 12 * scale
    return (f'<g transform="translate({cx-off:.1f},{cy-off:.1f}) scale({scale})" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'{_inner(ICONS / f"{name}.svg")}</g>')


def brand(name, cx, cy, color, scale=0.95):
    """Simple Icons brand mark — filled, centred on (cx, cy)."""
    off = 12 * scale
    return (f'<g transform="translate({cx-off:.1f},{cy-off:.1f}) scale({scale})" '
            f'fill="{color}" stroke="none">{_inner(ICONS / f"si-{name}.svg")}</g>')


def tag(x, y, label, color, mark, filled=True, big=False):
    """A small pill naming the tool that powers a step.

    Node tags run tight so two of them fit inside a 178px box; the lane-level
    badge uses `big` since it stands alone.
    """
    fs, pad, per, h, sc = (14, 30, 7.9, 30, 0.72) if big else (11.5, 24, 7.0, 25, 0.56)
    w = pad + len(label) * per
    g = brand(mark, x + pad * 0.62, y + h / 2, color, sc) if filled \
        else icon(mark, x + pad * 0.62, y + h / 2, color, sc)
    return "\n  ".join([
        f'<rect x="{x}" y="{y}" width="{w:.0f}" height="{h}" rx="{h/2}" fill="#fff" '
        f'stroke="{color}" stroke-width="1.4" opacity="0.95"/>', g,
        f'<text x="{x+pad+1:.0f}" y="{y+h/2+4:.0f}" style="font-size:{fs}px;font-weight:700;'
        f'fill:{color};font-family:-apple-system,Helvetica,Arial,sans-serif">{label}</text>',
    ]), w


def node(x, y, w, h, color, ic, title, desc, tags=(), mono=True, tsize=20):
    """icon · identifier · plain-English lines · tool tags."""
    cx = x + w / 2
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#fff" '
           f'stroke="{color}" stroke-width="2.5"/>',
           icon(ic, cx, y + 40, color),
           f'<text class="{"node" if mono else "plain"}" x="{cx}" y="{y+92}" '
           f'text-anchor="middle" style="font-size:{tsize}px">{title}</text>']
    for i, line in enumerate(desc):
        out.append(f'<text class="desc" x="{cx}" y="{y+117+i*18}" text-anchor="middle">{line}</text>')
    if tags:
        rendered = [tag(0, 0, lb, c, mk, f) for lb, c, mk, f in tags]
        total = sum(w_ for _, w_ in rendered) + 8 * (len(rendered) - 1)
        tx = cx - total / 2
        ty = y + h - 40
        for (lb, c, mk, f), (_, w_) in zip(tags, rendered):
            out.append(tag(tx, ty, lb, c, mk, f)[0])
            tx += w_ + 8
    return "\n  ".join(out)


P, A = [], None
A = P.append

# ── swimlanes ────────────────────────────────────────────────────────────────
for x, y, w, h, fill, stroke in [
    (40, 110, 230, 770, "#faf5ff", "#c084fc"),
    (305, 110, 1090, 770, "url(#lane2)", "#93c5fd"),
    (1435, 110, 430, 460, "#f0fdf4", "#86efac"),
    (1435, 610, 430, 300, "#fffbeb", "#fcd34d"),
]:
    A(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" '
      f'stroke="{stroke}" stroke-dasharray="7 5" stroke-width="1.5"/>')

A('<text class="lane" x="40"   y="88"  fill="#7e22ce">1. Trigger</text>')
A('<text class="lane" x="850"  y="88"  fill="#1d4ed8" text-anchor="middle">'
  '2. Agent pipeline — 6 LangGraph nodes</text>')
A('<text class="lane" x="1435" y="88"  fill="#15803d">3. Human review</text>')
A('<text class="lane" x="1435" y="597" fill="#b45309">4. Escalation</text>')

# orchestration badge for the lane as a whole
A(tag(330, 128, "LangGraph orchestrates the loop", LANGCHAIN, "langchain", True, big=True)[0])
A(tag(620, 128, "Claude / GPT drives planner, coding, debugging", MODEL,
      "sparkles", False, big=True)[0])

# ── nodes ────────────────────────────────────────────────────────────────────
GH_TAG    = ("GitHub API", GH, "github", True)
MODEL_TAG = ("Claude / GPT", MODEL, "sparkles", False)

A(node(60, 430, 190, 190, PURPLE, "circle-dot", "GitHub issue",
       ["Someone reports", "a bug"], [GH_TAG], mono=False))

A(node(340, 430, 178, 190, BLUE, "file-search", "planner",
       ["Finds the code that", "matters, plans the fix"],
       [("pgvector retrieval", PG, "postgresql", True)]))

A(node(555, 430, 178, 190, BLUE, "square-pen", "coding",
       ["Edits the files", "the plan named"]))

A(node(770, 430, 178, 190, BLUE, "shield-check", "testing",
       ["Runs the suite with", "no network access"],
       [("Docker", DOCKER, "docker", True)]))

A(node(600, 185, 178, 180, BLUE, "bug", "debugging",
       ["Reads the failure,", "works out the cause"], tsize=19))

A(node(1120, 430, 178, 190, GREEN, "git-pull-request", "pr_creation",
       ["Pushes a branch,", "opens the pull request"], [GH_TAG], tsize=19))

A(node(1120, 700, 178, 190, AMBER, "triangle-alert", "human_escalation",
       ["Pushes the partial work", "as a draft PR"], [GH_TAG], tsize=16))

A(node(1470, 200, 360, 165, GREEN, "users", "QA / reviewer",
       ["A person reads the diff"], mono=False))

A(node(1470, 395, 360, 165, GREEN, "git-merge", "Manual merge",
       ["Only a human can merge —", "the agent has no merge path"], mono=False))

A(node(1470, 700, 360, 190, AMBER, "user-round-check", "Human takes over",
       ["Inherits real code and a written",
        "account of what was tried"], mono=False))

# ── decision diamond ─────────────────────────────────────────────────────────
A('<path d="M1030,473 L1082,525 L1030,577 L978,525 Z" fill="#fff" stroke="#64748b" stroke-width="2.5"/>')
A('<text class="desc" x="1030" y="520" text-anchor="middle" style="font-size:13px">tests</text>')
A('<text class="desc" x="1030" y="536" text-anchor="middle" style="font-size:13px">pass?</text>')

# ── edges ────────────────────────────────────────────────────────────────────
for d in [
    "M250,525 L336,525",                          # issue -> planner
    "M518,525 L551,525",                          # planner -> coding
    "M733,525 L766,525",                          # coding -> testing
    "M948,525 L974,525",                          # testing -> gate
    "M1082,525 L1116,525",                        # gate -> pr_creation   (pass)
    "M1030,577 L1030,795 L1116,795",              # gate -> escalation    (give up)
    "M1030,473 L1030,275 L782,275",               # gate -> debugging     (fail)
    "M650,365 L650,426",                          # debugging -> coding   (retry)
    "M1298,525 L1345,525 L1345,282 L1466,282",    # pr -> reviewer
    "M1650,365 L1650,391",                        # reviewer -> merge
    "M1298,795 L1466,795",                        # escalation -> human
]:
    A(f'<path class="flow" d="{d}" marker-end="url(#a)"/>')

for x, y, label, mid in [(1099, 512, "pass", True), (1048, 781, "give up", False),
                         (1045, 340, "fail", False), (662, 402, "retry", False)]:
    A(f'<text class="edge" x="{x}" y="{y}"{" text-anchor=\"middle\"" if mid else ""}>{label}</text>')

A('<text x="1865" y="975" text-anchor="end" style="font-size:12px;fill:#94a3b8">'
  'Icons: Lucide (ISC) · Simple Icons (CC0)</text>')

(HERE / "diagram.html").write_text(f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  body {{ margin:0; background:#fff; display:inline-block; }}
  svg  {{ display:block; font-family:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif; }}
  .lane {{ font-size:22px; font-weight:700; }}
  .node {{ font-weight:700; fill:#0f172a; font-family:ui-monospace,"SF Mono",Menlo,monospace; }}
  .plain{{ font-weight:700; fill:#0f172a; }}
  .desc {{ font-size:13.5px; fill:#64748b; }}
  .edge {{ font-size:15px; font-weight:700; fill:#334155; }}
  .flow {{ stroke:{SLATE}; stroke-width:2.2; fill:none; }}
</style></head><body>
<svg id="d" width="1900" height="1010" viewBox="0 0 1900 1010">
  <defs>
    <linearGradient id="lane2" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#eff6ff"/><stop offset="100%" stop-color="#f5f3ff"/>
    </linearGradient>
    <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
    orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{SLATE}"/></marker></defs>
  {chr(10).join("  " + p for p in P)}
</svg>
<script>
  const s = document.getElementById('d');
  s.setAttribute('width', 1900 * 2); s.setAttribute('height', 1010 * 2);
</script>
</body></html>
""")
print("built diagram.html")
