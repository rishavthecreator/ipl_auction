"""CPU bidder — balanced, stepwise, budget-aware for You vs CPU."""

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


def _role_counts(team: Dict[str, Any]) -> Dict[str, int]:
    counts = {r: 0 for r in ROLE_TARGETS}
    for p in team.get("players") or []:
        r = p.get("role")
        if r in counts:
            counts[r] += 1
    return counts


def _marquee_count(team: Dict[str, Any]) -> int:
    return sum(1 for p in (team.get("players") or []) if p.get("tier") == "marquee")


def _pace_ceiling(team: Dict[str, Any], max_bid: float) -> float:
    """Don't dump the purse early — keep enough for remaining min squad."""
    cfg = current_app.config
    min_squad = int(cfg["MIN_SQUAD_SIZE"])
    max_squad = int(cfg["MAX_SQUAD_SIZE"])
    reserve_unit = float(cfg.get("RESERVE_BASE_PRICE", 0.5))
    have = len(team.get("players") or [])
    need_min = max(0, min_squad - have - 1)  # after this buy
    remaining = float(team.get("remaining_budget") if team.get("remaining_budget") is not None
                      else (team.get("budget", 100) - team.get("spent", 0)))
    # Keep reserve for min slots + a soft buffer for later quality picks
    soft_buffer = 8.0
    reserved = need_min * reserve_unit + soft_buffer
    affordable = max(0.0, remaining - reserved)
    # Target average spend for remaining max capacity
    slots_left_after = max(1, max_squad - have - 1)
    avg_target = affordable / max(1, slots_left_after + 1)
    # Early auction: be stingier (first third of squad)
    if have < 6:
        avg_target *= 0.75
    elif have < 12:
        avg_target *= 0.9
    return round(min(max_bid, max(avg_target * 1.4, avg_target)) * 2) / 2


def _tier_hard_cap(team: Dict[str, Any], player: Dict[str, Any], rng: random.Random) -> float:
    cfg = current_app.config
    tier = player.get("tier") or "capped"
    base = float(player.get("base_price") or 1.0)

    if tier == "marquee":
        elite_n = int(cfg.get("CPU_MARQUEE_ELITE_COUNT", 2))
        elite_max = float(cfg.get("CPU_MARQUEE_ELITE_MAX", 20.0))
        normal_max = float(cfg.get("CPU_MARQUEE_NORMAL_MAX", 15.0))
        normal_min = float(cfg.get("CPU_MARQUEE_NORMAL_MIN", 10.0))
        owned = _marquee_count(team)
        if owned < elite_n:
            # One of the two "star" buys — up to 20, often less
            return round(rng.uniform(12.0, elite_max) * 2) / 2
        # Additional marquees: 10–15 band
        return round(rng.uniform(normal_min, normal_max) * 2) / 2

    if tier == "capped":
        return round(rng.uniform(3.0, 8.0) * 2) / 2

    # uncapped
    return round(rng.uniform(base, 3.0) * 2) / 2


def interest_score(team: Dict[str, Any], player: Dict[str, Any], rng: random.Random) -> float:
    role = player.get("role") or "batter"
    tier = player.get("tier") or "capped"
    counts = _role_counts(team)
    need = max(0, ROLE_TARGETS.get(role, 4) - counts.get(role, 0))
    min_squad = int(current_app.config["MIN_SQUAD_SIZE"])
    squad_gap = max(0, min_squad - len(team.get("players") or []))

    score = 0.15 + min(0.35, need * 0.12)
    if squad_gap > 0:
        score += 0.15
    if tier == "marquee":
        owned = _marquee_count(team)
        if owned < 2:
            score += 0.2
        else:
            score += 0.05
    elif tier == "capped":
        score += 0.08
    score += rng.uniform(-0.1, 0.1)
    return max(0.0, min(1.0, score))


def ensure_ceilings(state, teams_public, player) -> Dict[int, float]:
    idx = state.get("current_index", 0)
    cache = state.setdefault("_cpu_ceilings", {})
    key = str(idx)
    if key in cache:
        return {int(k): float(v) for k, v in cache[key].items()}

    seed = (state.get("pool_seed") or 0) + idx * 97
    ceilings = {}
    for t in teams_public:
        if not t.get("is_cpu"):
            continue
        rng = random.Random(seed + t["id"] * 13)
        interest = interest_score(t, player, rng)
        # Sit out sometimes when interest low (still engage early auctions)
        sit = 0.22 - interest * 0.2
        if rng.random() < sit:
            ceilings[t["id"]] = 0.0
            continue
        max_bid = float(t.get("max_bid") or 0)
        hard = _tier_hard_cap(t, player, rng)
        pace = _pace_ceiling(t, max_bid)
        # Interest scales within the hard/pace envelope
        ceiling = min(max_bid, hard, pace)
        ceiling = round(ceiling * (0.55 + interest * 0.45) * 2) / 2
        base = float(player.get("base_price") or 1.0)
        if ceiling < base:
            ceilings[t["id"]] = 0.0
        else:
            ceilings[t["id"]] = ceiling
    cache[key] = {str(k): v for k, v in ceilings.items()}
    return ceilings


def choose_cpu_bid(state, teams_public, player, next_min, step):
    """One stepwise raise at most. Yield after enough human raises."""
    human_id = state.get("human_team_id")
    bid = state.get("current_bid") or {}
    leader = bid.get("team_id")
    human_raises = int(state.get("human_raises_on_current") or 0)
    current_price = float((bid or {}).get("amount") or 0)
    tier = (player or {}).get("tier") or "capped"
    # Yield ONLY for normal (capped/uncapped) players once price is already high (>= 10 Cr)
    # after ~2–3 human raises. Marquee and sub-10 Cr fights: CPU stays competitive / smart.
    is_normal = tier in ("capped", "uncapped")
    ceilings = ensure_ceilings(state, teams_public, player)
    seed = (state.get("pool_seed") or 0) + int(state.get("current_index") or 0) * 7
    rng = random.Random(seed + human_raises * 19)
    base_yield = int(current_app.config.get("CPU_HUMAN_RAISES_BEFORE_YIELD", 3))
    # Randomize around 2–3 raises so it feels natural, not scripted
    yield_after = max(2, min(3, base_yield - (1 if rng.random() < 0.45 else 0)))
    if (
        is_normal
        and current_price + 1e-9 >= 10.0
        and human_raises >= yield_after
        and leader == human_id
    ):
        return None

    candidates = []
    for t in teams_public:
        if not t.get("is_cpu"):
            continue
        if human_id and t["id"] == human_id:
            continue
        if not t.get("can_bid"):
            continue
        if leader == t["id"]:
            continue
        ceiling = float(ceilings.get(t["id"], 0.0))
        if ceiling + 1e-9 < next_min:
            continue
        if float(t.get("max_bid") or 0) + 1e-9 < next_min:
            continue

        # Always raise by exactly one step (or open at base via next_min)
        amount = round(float(next_min), 2)
        amount = min(amount, ceiling, float(t["max_bid"]))
        amount = round(amount * 2) / 2
        if amount + 1e-9 < next_min:
            continue

        # Duel vs human: stepwise chase within ceiling
        if leader == human_id and human_raises >= 1:
            if next_min > ceiling:
                continue
            ptier = player.get("tier") or "capped"
            if ptier == "marquee":
                # Stay in the fight while under ceiling / hard caps
                chase_p = 0.85 if human_raises <= 4 else 0.55
            elif next_min + 1e-9 < 10.0:
                # Below 10 Cr on normal players — keep pushing
                chase_p = 0.8
            else:
                # Normal player already expensive — fade after repeated raises
                chase_p = 0.55 if human_raises == 1 else (0.35 if human_raises == 2 else 0.15)
            if rng.random() > chase_p:
                continue

        candidates.append((ceiling - next_min, t["id"], amount))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, team_id, amount = candidates[0]
    return team_id, amount
