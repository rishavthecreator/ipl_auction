"""CPU bidder — value-based, paced, role-aware for You vs CPU."""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

ROLE_TARGETS = {
    "batter": 6,
    "wicket_keeper": 2,
    "all_rounder": 4,
    "bowler": 6,
}


def _round_cr(x: float) -> float:
    return round(float(x) * 2) / 2


def _cfg(key: str, default):
    return current_app.config.get(key, default)


def _remaining(team: Dict[str, Any]) -> float:
    if team.get("remaining_budget") is not None:
        return float(team["remaining_budget"])
    return float(team.get("budget", 100) - team.get("spent", 0))


def _role_counts(team: Dict[str, Any]) -> Dict[str, int]:
    counts = {r: 0 for r in ROLE_TARGETS}
    for p in team.get("players") or []:
        r = p.get("role")
        if r in counts:
            counts[r] += 1
    return counts


def _marquee_count(team: Dict[str, Any]) -> int:
    return sum(1 for p in (team.get("players") or []) if p.get("tier") == "marquee")


def attach_cpu_profiles(state: Dict[str, Any]) -> None:
    """Draw one aggression factor per CPU team for the whole auction."""
    seed = int(state.get("pool_seed") or 0)
    lo = float(_cfg("CPU_AGGRESSION_MIN", 0.85))
    hi = float(_cfg("CPU_AGGRESSION_MAX", 1.15))
    for t in state.get("teams") or []:
        if not t.get("is_cpu"):
            continue
        if t.get("cpu_aggression") is not None:
            continue
        rng = random.Random(seed + int(t["id"]) * 17)
        t["cpu_aggression"] = round(rng.uniform(lo, hi), 3)


def _core_index() -> Dict[str, int]:
    from services.auction import ALL_STARS_CORE
    return {name: i for i, name in enumerate(ALL_STARS_CORE)}


def _pool_rank_frac(state: Dict[str, Any], player: Dict[str, Any]) -> float:
    """0 = best in this pool's tier, 1 = worst. Cached on the in-memory state."""
    cache = state.setdefault("_cpu_ranks", {})
    pid = player.get("id")
    if pid in cache:
        return float(cache[pid])

    core = _core_index()
    by_tier: Dict[str, List[Dict[str, Any]]] = {}
    for p in state.get("auction_pool") or []:
        by_tier.setdefault(p.get("tier") or "capped", []).append(p)

    ranks: Dict[Any, float] = {}
    for _tier, items in by_tier.items():
        def sort_key(p: Dict[str, Any]):
            if p.get("source_list_no") is not None:
                return (0, int(p["source_list_no"]), int(p.get("id") or 0))
            name = p.get("name") or ""
            if name in core:
                return (1, core[name], int(p.get("id") or 0))
            return (2, int(p.get("id") or 0))

        ordered = sorted(items, key=sort_key)
        n = max(1, len(ordered) - 1)
        for i, p in enumerate(ordered):
            ranks[p["id"]] = i / n
    cache.update(ranks)
    return float(cache.get(pid, 0.5))


def player_fair_value(state: Dict[str, Any], player: Dict[str, Any]) -> float:
    """Fair price (Cr) from tier band + rank in this pool. Never below base."""
    tier = player.get("tier") or "capped"
    base = float(player.get("base_price") or 1.0)
    bands = {
        "marquee": (
            float(_cfg("CPU_VALUE_MARQUEE_MIN", 10.0)),
            float(_cfg("CPU_VALUE_MARQUEE_MAX", 14.0)),
        ),
        "capped": (
            float(_cfg("CPU_VALUE_CAPPED_MIN", 2.0)),
            float(_cfg("CPU_VALUE_CAPPED_MAX", 5.0)),
        ),
        "uncapped": (
            float(_cfg("CPU_VALUE_UNCAPPED_MIN", 0.5)),
            float(_cfg("CPU_VALUE_UNCAPPED_MAX", 1.5)),
        ),
    }
    lo, hi = bands.get(tier, bands["capped"])
    frac = _pool_rank_frac(state, player)
    value = hi - frac * (hi - lo)
    return _round_cr(max(base, value))


def _role_multiplier(team: Dict[str, Any], player: Dict[str, Any]) -> float:
    role = player.get("role") or "batter"
    counts = _role_counts(team)
    have_role = counts.get(role, 0)
    target = ROLE_TARGETS.get(role, 4)
    need_mult = float(_cfg("CPU_ROLE_NEED_MULT", 1.2))
    full_mult = float(_cfg("CPU_ROLE_FULL_MULT", 0.7))
    min_squad = int(current_app.config["MIN_SQUAD_SIZE"])
    squad_gap = max(0, min_squad - len(team.get("players") or []))

    if have_role >= target:
        return full_mult
    if have_role == 0 and role == "wicket_keeper":
        return need_mult
    if have_role == 0 and squad_gap > 0:
        return min(need_mult, 1.15)
    if have_role < target:
        return 1.1
    return 1.0


def _pace_ceiling(team: Dict[str, Any], player: Dict[str, Any], max_bid: float) -> float:
    """Purse pacing. First marquee is allowed up to a floor (~10 Cr)."""
    min_squad = int(current_app.config["MIN_SQUAD_SIZE"])
    max_squad = int(current_app.config["MAX_SQUAD_SIZE"])
    reserve_unit = float(current_app.config.get("RESERVE_BASE_PRICE", 0.5))
    have = len(team.get("players") or [])
    need_min = max(0, min_squad - have - 1)
    remaining = _remaining(team)
    reserved = need_min * reserve_unit
    affordable = max(0.0, remaining - reserved)
    slots_left_after = max(1, max_squad - have - 1)
    avg_target = affordable / max(1, slots_left_after + 1)

    is_marquee = (player.get("tier") or "") == "marquee"
    owned_m = _marquee_count(team)
    is_anchor = is_marquee and owned_m < 2
    if have < 6 and not is_anchor:
        avg_target *= 0.75
    elif have < 12 and not is_anchor:
        avg_target *= 0.9

    pace = max(avg_target * 1.4, avg_target)
    if is_marquee and owned_m == 0:
        floor = float(_cfg("CPU_FIRST_MARQUEE_PACE_FLOOR", 10.0))
        pace = max(pace, min(floor, affordable, max_bid))
    return _round_cr(min(max_bid, max(0.0, pace)))


def _should_sit_out(
    team: Dict[str, Any],
    player: Dict[str, Any],
    walk_cap: float,
    rng: random.Random,
) -> bool:
    role = player.get("role") or "batter"
    counts = _role_counts(team)
    have_role = counts.get(role, 0)
    target = ROLE_TARGETS.get(role, 4)
    min_squad = int(current_app.config["MIN_SQUAD_SIZE"])
    have = len(team.get("players") or [])
    squad_gap = max(0, min_squad - have)
    base = float(player.get("base_price") or 1.0)

    if (player.get("tier") or "") == "marquee" and _marquee_count(team) == 0:
        return False
    if have_role == 0 and role == "wicket_keeper":
        return False
    if squad_gap > 0 and have >= 10 and base <= float(_cfg("CPU_LATE_FILL_MAX", 1.0)):
        return False
    if have_role >= target and base > walk_cap + 1e-9:
        return True
    sit_p = float(_cfg("CPU_SIT_OUT_P", 0.05))
    return rng.random() < sit_p


def ensure_ceilings(state, teams_public, player) -> Dict[int, float]:
    attach_cpu_profiles(state)
    idx = state.get("current_index", 0)
    cache = state.setdefault("_cpu_ceilings", {})
    key = str(idx)
    if key in cache:
        return {int(k): float(v) for k, v in cache[key].items()}

    seed = (state.get("pool_seed") or 0) + idx * 97
    fair = player_fair_value(state, player)
    walk_mult = float(_cfg("CPU_WALK_MULT", 1.15))
    fill_max = float(_cfg("CPU_LATE_FILL_MAX", 1.0))
    min_squad = int(current_app.config["MIN_SQUAD_SIZE"])
    live_by_id = {t["id"]: t for t in (state.get("teams") or [])}
    ceilings = {}

    for t in teams_public:
        if not t.get("is_cpu"):
            continue
        live = live_by_id.get(t["id"]) or t
        rng = random.Random(seed + t["id"] * 13)
        agg = float(live.get("cpu_aggression") or t.get("cpu_aggression") or 1.0)
        role_m = _role_multiplier(t, player)
        max_bid = float(t.get("max_bid") or 0)
        pace = _pace_ceiling(t, player, max_bid)
        walk_cap = _round_cr(fair * agg * role_m * walk_mult)
        have = len(t.get("players") or [])
        squad_gap = max(0, min_squad - have)
        if squad_gap > 0 and have >= 10:
            walk_cap = max(walk_cap, min(fill_max, max_bid))
        ceiling = min(max_bid, walk_cap, pace)
        base = float(player.get("base_price") or 1.0)
        if _should_sit_out(t, player, walk_cap, rng) or ceiling + 1e-9 < base:
            ceilings[t["id"]] = 0.0
        else:
            ceilings[t["id"]] = max(0.0, ceiling)

    cache[key] = {str(k): v for k, v in ceilings.items()}
    # Persist aggression drawn on public copies back onto live state teams
    by_id = {t["id"]: t for t in (state.get("teams") or [])}
    for t in teams_public:
        live = by_id.get(t["id"])
        if live is not None and t.get("cpu_aggression") is not None:
            live["cpu_aggression"] = t["cpu_aggression"]
    return ceilings


def choose_cpu_bid(state, teams_public, player, next_min, step):
    """Open, fight, or walk using the value ceiling. One step. No human-only yield."""
    attach_cpu_profiles(state)
    bid = state.get("current_bid") or {}
    leader = bid.get("team_id")
    next_min = float(next_min)
    ceilings = ensure_ceilings(state, teams_public, player)

    candidates: List[Tuple[float, int, float]] = []
    for t in teams_public:
        if not t.get("is_cpu"):
            continue
        if not t.get("can_bid"):
            continue
        if leader == t["id"]:
            continue
        ceiling = float(ceilings.get(t["id"], 0.0))
        max_bid = float(t.get("max_bid") or 0)
        # Walk
        if next_min > ceiling + 1e-9:
            continue
        if next_min > max_bid + 1e-9:
            continue
        amount = _round_cr(min(next_min, ceiling, max_bid))
        if amount + 1e-9 < next_min:
            continue
        candidates.append((ceiling - next_min, t["id"], amount))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, team_id, amount = candidates[0]
    return team_id, amount
