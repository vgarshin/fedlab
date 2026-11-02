"""Analytics for one run: KPIs, grade and length distributions, best/worst works. Read-only, from the database."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from api.deps import CurrentUser, Db, State
from core import moodle
from core.db import LlmCall, Run, Team

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
GRADE_BIN = 10          # grade histogram: 0-9, 10-19, ... 90-100
LENGTH_BIN = 2000       # length histogram step in characters (with spaces)


def histogram(values: list[tuple[str, float]], width: int, upper: int | None = None, top_label: bool = False) -> list[dict]:
    """values: (team name, value). Bins [k*width, (k+1)*width); the last bin is open-ended when ``upper`` is set."""
    if not values:
        return []
    hi = upper if upper is not None else int(max(v for _, v in values) // width + 1) * width
    n = max(1, (hi + width - 1) // width) if upper is None else upper // width
    bins = [{"from": i * width, "to": (i + 1) * width - 1, "count": 0, "teams": []} for i in range(n)]
    for name, v in values:
        i = min(int(v // width), n - 1)
        bins[i]["count"] += 1
        if len(bins[i]["teams"]) < 8:
            bins[i]["teams"].append(name)
    if upper is not None:
        bins[-1]["to"] = upper if top_label else bins[-1]["to"]
        bins[-1]["open_end"] = True
    return bins


@router.get("")
async def analytics(state: State, _: CurrentUser, db: Db, run_id: int | None = None) -> dict:
    runs = (await db.execute(select(Run).order_by(Run.id.desc()))).scalars().all()
    if not runs:
        return {"runs": [], "run": None}
    run = next((r for r in runs if r.id == run_id), None) if run_id else runs[0]       # default: the latest run
    if run is None:
        raise HTTPException(404, "Run not found")
    teams = (await db.execute(select(Team).where(Team.run_id == run.id))).scalars().all()
    evaluated = [t for t in teams if t.grade_status == "EVALUATED" and t.grade_total is not None]
    dq = [t for t in teams if t.grade_status == "DISQUALIFIED"]
    spent = float((await db.execute(select(func.coalesce(func.sum(LlmCall.cost_rub), 0.0)).where(LlmCall.run_id == run.id))).scalar_one())

    members = None
    wkey = (run.source or {}).get("worksheet_key")
    if wkey and (raw := await state.stores.data.get(wkey)) is not None:
        names = {moodle.norm_team(t.name) for t in teams}
        members = sum(1 for r in moodle.parse_worksheet(raw) if moodle.norm_team(r.get("Group", "")) in names)

    def row(t: Team) -> dict:
        return {"id": t.id, "name": t.name, "grade": t.grade_total, "chars": t.chars_with_spaces, "ai": t.ai_probability}

    ranked = sorted(evaluated, key=lambda t: (-t.grade_total, t.name.casefold()))
    lengths = [(t.name, float(t.chars_with_spaces)) for t in teams if t.chars_with_spaces is not None]
    limit = state.settings.processing.char_limit
    avg = round(sum(t.grade_total for t in evaluated) / len(evaluated), 1) if evaluated else None
    return {
        "runs": [{"id": r.id, "name": r.name, "created_at": r.created_at} for r in runs],
        "run": {"id": run.id, "name": run.name, "status": run.status},
        "kpi": {"teams": len(teams), "members": members, "graded": len(evaluated), "disqualified": len(dq),
                "not_graded": len(teams) - len(evaluated) - len(dq), "avg_grade": avg,
                "median_grade": sorted(t.grade_total for t in evaluated)[len(evaluated) // 2] if evaluated else None,
                "spent_rub": round(spent, 2), "cost_per_work_rub": round(spent / len(teams), 2) if teams else None},
        "grade_hist": histogram([(t.name, float(t.grade_total)) for t in evaluated], GRADE_BIN, upper=100, top_label=True),
        "length_hist": histogram(lengths, LENGTH_BIN),
        "char_limit": limit, "over_limit": sum(1 for _, v in lengths if v > limit),
        "top": [row(t) for t in ranked[:5]],
        "bottom": [row(t) for t in list(reversed(ranked))[:5]],
    }
