"""IPL auction game logic and persistent state."""

from __future__ import annotations

import json
import os
import random
import threading
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

_lock = threading.RLock()
_state: Optional[Dict[str, Any]] = None

ROLE_LABELS = {
    'wicket_keeper': 'Wicket Keeper',
    'batter': 'Batter',
    'bowler': 'Bowler',
    'all_rounder': 'All-Rounder',
}

TIER_LABELS = {
    'marquee': 'Marquee',
    'capped': 'Capped',
    'uncapped': 'Uncapped',
}

# Within each tier (and for round-1 overall structure).
ROLE_ORDER = ['batter', 'wicket_keeper', 'all_rounder', 'bowler']
TIER_ORDER = ['marquee', 'capped', 'uncapped']

# Always included in All Stars pools when present in the roster.
ALL_STARS_CORE = [
    'Virat Kohli', 'Rohit Sharma', 'MS Dhoni', 'Jasprit Bumrah', 'Hardik Pandya',
    'Suryakumar Yadav', 'Ravindra Jadeja', 'Shubman Gill', 'KL Rahul', 'Rishabh Pant',
    'Sanju Samson', 'Jos Buttler', 'Andre Russell', 'Sunil Narine', 'Rashid Khan',
    'Pat Cummins', 'Mitchell Starc', 'Kagiso Rabada', 'Trent Boult', 'Yuzvendra Chahal',
    'Axar Patel', 'Shreyas Iyer', 'Yashasvi Jaiswal', 'Ruturaj Gaikwad', 'Heinrich Klaasen',
]


def _players_path(mode: str = 'standard') -> str:
    if mode == 'all_stars':
        return current_app.config['ALL_STARS_FILE']
    return current_app.config['PLAYERS_FILE']


def _state_path() -> str:
    return current_app.config['STATE_FILE']


def _normalize_mode(mode: Optional[str]) -> str:
    m = (mode or 'standard').strip().lower()
    return 'all_stars' if m in ('all_stars', 'all-stars', 'allstars') else 'standard'


def _tier_base_price(tier: str) -> float:
    cfg = current_app.config
    mapping = {
        'marquee': float(cfg['BASE_PRICE_MARQUEE']),
        'capped': float(cfg['BASE_PRICE_CAPPED']),
        'uncapped': float(cfg['BASE_PRICE_UNCAPPED']),
    }
    return mapping.get(tier, float(cfg['BASE_PRICE_CAPPED']))


def load_master_players(mode: str = 'standard') -> List[Dict[str, Any]]:
    mode = _normalize_mode(mode)
    with open(_players_path(mode), 'r', encoding='utf-8') as f:
        players = json.load(f)
    for p in players:
        tier = p.get('tier') or ('marquee' if mode == 'all_stars' else 'capped')
        if mode == 'all_stars':
            tier = 'marquee'
        p['tier'] = tier
        p['tier_label'] = TIER_LABELS.get(tier, tier)
        p['role_label'] = ROLE_LABELS.get(p['role'], p['role'])
        p['base_price'] = float(
            p.get('base_price') or _tier_base_price(tier)
        )
        if mode == 'all_stars':
            p['base_price'] = float(current_app.config['BASE_PRICE_MARQUEE'])
            p['all_star'] = True
        p['backup_wk'] = False
    return players


def _empty_state() -> Dict[str, Any]:
    return {
        'status': 'setup',  # setup | auction | finished
        'num_teams': 0,
        'teams': [],
        'auction_pool': [],
        'sold': [],
        'unsold': [],
        'round1_unsold': [],
        'current_index': 0,
        'current_bid': None,
        'history': [],
        'round': 1,
        'pool_seed': None,
        'auction_mode': 'standard',
        'solo_mode': False,
        'human_team_id': None,
        'human_raises_on_current': 0,
    }


def _default_team(team_id: int, name: str, is_cpu: bool = False) -> Dict[str, Any]:
    return {
        'id': team_id,
        'name': name,
        'budget': float(current_app.config['TEAM_BUDGET']),
        'spent': 0.0,
        'players': [],
        'is_cpu': bool(is_cpu),
    }


def get_state() -> Dict[str, Any]:
    global _state
    with _lock:
        if _state is None:
            path = _state_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    _state = json.load(f)
                _state.setdefault('unsold', [])
                _state.setdefault('round1_unsold', [])
                _state.setdefault('history', [])
                _state.setdefault('round', 1)
                _state.setdefault('pool_seed', None)
                _state.setdefault('auction_mode', 'standard')
                _state.setdefault('solo_mode', False)
                _state.setdefault('human_team_id', None)
                _state.setdefault('human_raises_on_current', 0)
            else:
                _state = _empty_state()
        return deepcopy(_state)


def _save(state: Dict[str, Any]) -> None:
    global _state
    cache = None
    if isinstance(state, dict):
        cache = state.get('_cpu_ceilings')
    _state = deepcopy(state)
    _state.pop('_cpu_ceilings', None)
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(_state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # Keep ephemeral CPU ceiling cache in memory only
    if cache is not None:
        _state['_cpu_ceilings'] = cache


def reset_auction() -> Dict[str, Any]:
    with _lock:
        state = _empty_state()
        _save(state)
        return deepcopy(state)


def _bucket_key(p: Dict[str, Any]) -> Tuple[str, str]:
    return (p.get('tier') or 'capped', p['role'])


def select_balanced_pool(
    master: List[Dict[str, Any]],
    pool_size: int,
    seed: Optional[int] = None,
    ensure_names: Optional[List[str]] = None,
    uncapped_target: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Pick pool_size players balanced across tier x role; force uncapped_target when set."""
    if pool_size > len(master):
        raise ValueError(
            f'Need {pool_size} players but master roster only has {len(master)}. '
            f'Reduce number of teams.'
        )
    rng = random.Random(seed)

    must_ids = set()
    by_name = {p['name']: p for p in master}
    for name in (ensure_names or []):
        if name in by_name:
            must_ids.add(by_name[name]['id'])

    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for p in master:
        buckets.setdefault(_bucket_key(p), []).append(deepcopy(p))
    for key in buckets:
        # Keep ensured players first within each bucket, shuffle the rest
        forced = [p for p in buckets[key] if p['id'] in must_ids]
        rest = [p for p in buckets[key] if p['id'] not in must_ids]
        rng.shuffle(rest)
        buckets[key] = forced + rest

    total = len(master)
    # Proportional targets
    targets: Dict[Tuple[str, str], int] = {}
    for key, items in buckets.items():
        targets[key] = int(round(pool_size * len(items) / total))

    # Force-include all marquees when they fit
    marquee_keys = [k for k in buckets if k[0] == 'marquee']
    marquee_total = sum(len(buckets[k]) for k in marquee_keys)
    if marquee_total <= pool_size:
        for k in marquee_keys:
            targets[k] = len(buckets[k])

    # Force uncapped = uncapped_target (teams x 5)
    if uncapped_target is not None:
        u_keys = [k for k in buckets if k[0] == 'uncapped']
        u_avail = sum(len(buckets[k]) for k in u_keys)
        want_u = max(0, min(int(uncapped_target), u_avail, pool_size))
        for k in u_keys:
            targets[k] = 0
        if want_u and u_keys:
            weights = {k: len(buckets[k]) for k in u_keys}
            total_w = sum(weights.values()) or 1
            raw = {k: want_u * weights[k] / total_w for k in u_keys}
            base_alloc = {k: int(raw[k]) for k in u_keys}
            rem = want_u - sum(base_alloc.values())
            for k in sorted(u_keys, key=lambda x: raw[x] - base_alloc[x], reverse=True):
                if rem <= 0:
                    break
                if base_alloc[k] < len(buckets[k]):
                    base_alloc[k] += 1
                    rem -= 1
            for k, n in base_alloc.items():
                targets[k] = min(n, len(buckets[k]))

    # Clamp to availability
    for k in list(targets):
        targets[k] = min(targets[k], len(buckets.get(k, [])))

    # Fix sum to pool_size
    def _sum_targets() -> int:
        return sum(targets.values())

    # Drop zero-empty keys that somehow appeared
    keys = sorted(buckets.keys(), key=lambda k: (TIER_ORDER.index(k[0]) if k[0] in TIER_ORDER else 99,
                                                 ROLE_ORDER.index(k[1]) if k[1] in ROLE_ORDER else 99))

    while _sum_targets() > pool_size:
        # Reduce largest non-marquee buckets first
        candidates = [k for k in keys if targets.get(k, 0) > (len(buckets[k]) if k[0] == 'marquee' and marquee_total <= pool_size else 0)]
        # Prefer reducing uncapped then capped
        candidates = sorted(
            [k for k in keys if targets.get(k, 0) > 0 and not (k[0] == 'marquee' and marquee_total <= pool_size and targets[k] <= len(buckets[k]))],
            key=lambda k: (0 if k[0] == 'marquee' else 1, targets[k]),
            reverse=True,
        )
        # Never drop below full marquee take if forcing
        trimmed = False
        for k in sorted(keys, key=lambda k: (0 if k[0] != 'marquee' else 1, targets.get(k, 0)), reverse=True):
            min_keep = len(buckets[k]) if (k[0] == 'marquee' and marquee_total <= pool_size) else 0
            if targets.get(k, 0) > min_keep:
                targets[k] -= 1
                trimmed = True
                break
        if not trimmed:
            break

    while _sum_targets() < pool_size:
        # Add to buckets with most remaining capacity
        candidates = sorted(
            keys,
            key=lambda k: len(buckets[k]) - targets.get(k, 0),
            reverse=True,
        )
        grew = False
        for k in candidates:
            if targets.get(k, 0) < len(buckets[k]):
                targets[k] = targets.get(k, 0) + 1
                grew = True
                break
        if not grew:
            break

    # Ensure forced players' buckets have enough target slots
    forced_need: Dict[Tuple[str, str], int] = {}
    for p in master:
        if p['id'] in must_ids:
            k = _bucket_key(p)
            forced_need[k] = forced_need.get(k, 0) + 1
    for k, need in forced_need.items():
        targets[k] = max(targets.get(k, 0), need)
        targets[k] = min(targets[k], len(buckets.get(k, [])))

    # Re-balance sum after forced bumps
    while sum(targets.values()) > pool_size:
        trimmed = False
        for k in sorted(keys, key=lambda k: (1 if k in forced_need else 0, targets.get(k, 0)), reverse=True):
            min_keep = max(
                forced_need.get(k, 0),
                len(buckets[k]) if (k[0] == 'marquee' and marquee_total <= pool_size) else 0,
            )
            if targets.get(k, 0) > min_keep:
                targets[k] -= 1
                trimmed = True
                break
        if not trimmed:
            break
    while sum(targets.values()) < pool_size:
        grew = False
        for k in sorted(keys, key=lambda k: len(buckets[k]) - targets.get(k, 0), reverse=True):
            if targets.get(k, 0) < len(buckets[k]):
                targets[k] = targets.get(k, 0) + 1
                grew = True
                break
        if not grew:
            break


    # Re-enforce uncapped quota after balancing (do not let grow/shrink drift it)
    if uncapped_target is not None:
        u_keys = [k for k in keys if k[0] == 'uncapped']
        u_avail = sum(len(buckets[k]) for k in u_keys)
        want_u = max(0, min(int(uncapped_target), u_avail, pool_size))
        cur_u = sum(targets.get(k, 0) for k in u_keys)
        if cur_u > want_u:
            overflow = cur_u - want_u
            for k in sorted(u_keys, key=lambda x: targets.get(x, 0), reverse=True):
                while overflow > 0 and targets.get(k, 0) > 0:
                    targets[k] -= 1
                    overflow -= 1
        elif cur_u < want_u:
            need = want_u - cur_u
            for k in sorted(u_keys, key=lambda x: len(buckets[x]) - targets.get(x, 0), reverse=True):
                while need > 0 and targets.get(k, 0) < len(buckets[k]):
                    targets[k] = targets.get(k, 0) + 1
                    need -= 1
        # Fix total back to pool_size using non-uncapped only
        non_u = [k for k in keys if k[0] != 'uncapped']
        while sum(targets.values()) > pool_size:
            trimmed = False
            for k in sorted(non_u, key=lambda x: targets.get(x, 0), reverse=True):
                min_keep = len(buckets[k]) if (k[0] == 'marquee' and marquee_total <= pool_size) else 0
                if k in (ensure_names and [] or []):
                    pass
                if targets.get(k, 0) > min_keep:
                    targets[k] -= 1
                    trimmed = True
                    break
            if not trimmed:
                break
        while sum(targets.values()) < pool_size:
            grew = False
            for k in sorted(non_u, key=lambda x: len(buckets[x]) - targets.get(x, 0), reverse=True):
                if targets.get(k, 0) < len(buckets[k]):
                    targets[k] = targets.get(k, 0) + 1
                    grew = True
                    break
            if not grew:
                break

    selected: List[Dict[str, Any]] = []
    for k in keys:
        n = targets.get(k, 0)
        selected.extend(buckets[k][:n])

    # Safety top-up / trim — never drop ensured names
    selected_ids = {p['id'] for p in selected}
    missing_must = [deepcopy(by_name[n]) for n in (ensure_names or []) if n in by_name and by_name[n]['id'] not in selected_ids]
    if missing_must:
        # Drop non-must to make room
        selected = [p for p in selected if p['id'] in must_ids] + [p for p in selected if p['id'] not in must_ids]
        overflow = len(selected) + len(missing_must) - pool_size
        if overflow > 0:
            droppable = [p for p in selected if p['id'] not in must_ids]
            selected = [p for p in selected if p['id'] in must_ids] + droppable[overflow:]
        selected.extend(missing_must)
        selected_ids = {p['id'] for p in selected}

    if len(selected) < pool_size:
        leftovers = [deepcopy(p) for p in master if p['id'] not in selected_ids]
        rng.shuffle(leftovers)
        selected.extend(leftovers[: pool_size - len(selected)])
    if len(selected) > pool_size:
        must = [p for p in selected if p['id'] in must_ids]
        rest = [p for p in selected if p['id'] not in must_ids]
        rest.sort(key=lambda p: (0 if p['tier'] == 'marquee' else 1, 0 if p['tier'] == 'capped' else 1, rng.random()))
        need = pool_size - len(must)
        selected = must + rest[:max(0, need)]

    return selected


def order_round1_pool(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Marquee → Capped → Uncapped; within each: Batter → WK → AR → Bowler."""
    by: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for p in pool:
        by.setdefault(_bucket_key(p), []).append(p)
    ordered: List[Dict[str, Any]] = []
    for tier in TIER_ORDER:
        for role in ROLE_ORDER:
            ordered.extend(by.get((tier, role), []))
    # Any unexpected buckets last
    used = set(id(x) for x in ordered)
    for items in by.values():
        for p in items:
            if id(p) not in used:
                ordered.append(p)
    return ordered


def _pool_grouped_by_tier_role(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups = []
    by: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for p in pool:
        by.setdefault(_bucket_key(p), []).append(p)
    for tier in TIER_ORDER:
        for role in ROLE_ORDER:
            items = by.get((tier, role), [])
            if not items:
                continue
            groups.append({
                'tier': tier,
                'tier_label': TIER_LABELS.get(tier, tier),
                'role': role,
                'label': ROLE_LABELS.get(role, role),
                'count': len(items),
                'players': items,
            })
    return groups


def _tier_role_counts(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    tiers = {t: 0 for t in TIER_ORDER}
    roles = {r: 0 for r in ROLE_ORDER}
    matrix = []
    by: Dict[Tuple[str, str], int] = {}
    for p in players:
        tiers[p['tier']] = tiers.get(p['tier'], 0) + 1
        roles[p['role']] = roles.get(p['role'], 0) + 1
        by[_bucket_key(p)] = by.get(_bucket_key(p), 0) + 1
    for tier in TIER_ORDER:
        for role in ROLE_ORDER:
            n = by.get((tier, role), 0)
            if n:
                matrix.append({
                    'tier': tier,
                    'tier_label': TIER_LABELS[tier],
                    'role': role,
                    'role_label': ROLE_LABELS[role],
                    'count': n,
                })
    return {'by_tier': tiers, 'by_role': roles, 'matrix': matrix}


def _uncapped_quota(num_teams: int, mode: str):
    if mode == 'all_stars':
        return None
    return int(num_teams) * int(current_app.config.get('UNCAPPED_PER_TEAM', 5))


def preview_pool(
    num_teams: int = 0,
    seed: Optional[int] = None,
    mode: str = 'standard',
) -> Dict[str, Any]:
    """Build a balanced auction pool preview for the chosen team count."""
    mode = _normalize_mode(mode)
    min_t = current_app.config['MIN_TEAMS']
    max_t = current_app.config['MAX_TEAMS']
    per = int(current_app.config['PLAYERS_PER_TEAM_POOL'])
    master = load_master_players(mode)

    if num_teams < min_t or num_teams > max_t:
        num_teams = max(min_t, min(num_teams or min_t, max_t))

    desired = num_teams * per
    pool_size = min(desired, len(master))
    if seed is None:
        seed = random.randint(1, 2_000_000_000)
    ensure = ALL_STARS_CORE if mode == 'all_stars' else None
    uq = _uncapped_quota(num_teams, mode)
    selected = select_balanced_pool(
        master, pool_size, seed=seed, ensure_names=ensure, uncapped_target=uq,
    )
    ordered = order_round1_pool(selected)
    counts = _tier_role_counts(ordered)

    if mode == 'all_stars':
        order_txt = (
            'All Stars: all players at Marquee base (2 Cr). '
            'Order: Batter → WK → All-Rounder → Bowler. '
            'Round 2 (if needed): unsold only, random order.'
        )
    else:
        order_txt = (
            'Round 1: Marquee → Capped → Uncapped; '
            'within each: Batter → WK → All-Rounder → Bowler. '
            'Round 2 (if needed): unsold only, random order.'
        )

    return {
        'num_teams': num_teams,
        'auction_mode': mode,
        'pool_size': len(ordered),
        'desired_pool_size': desired,
        'pool_capped_to_roster': pool_size < desired,
        'players_per_team_pool': per,
        'pool_seed': seed,
        'squad_size': current_app.config['MAX_SQUAD_SIZE'],
        'min_squad_size': current_app.config['MIN_SQUAD_SIZE'],
        'max_squad_size': current_app.config['MAX_SQUAD_SIZE'],
        'players': ordered,
        'by_tier_role': _pool_grouped_by_tier_role(ordered),
        'counts': counts,
        'role_labels': ROLE_LABELS,
        'tier_labels': TIER_LABELS,
        'auction_order': order_txt,
        'base_prices': {
            'marquee': float(current_app.config['BASE_PRICE_MARQUEE']),
            'capped': float(current_app.config['BASE_PRICE_CAPPED']),
            'uncapped': float(current_app.config['BASE_PRICE_UNCAPPED']),
        },
        'master_total': len(master),
    }


def start_auction(
    num_teams: int,
    team_names: Optional[List[str]] = None,
    seed: Optional[int] = None,
    mode: str = 'standard',
    solo_mode: bool = False,
    human_team_id: Optional[int] = None,
) -> Dict[str, Any]:
    mode = _normalize_mode(mode)
    min_t = current_app.config['MIN_TEAMS']
    max_t = current_app.config['MAX_TEAMS']
    per = int(current_app.config['PLAYERS_PER_TEAM_POOL'])

    if num_teams < min_t or num_teams > max_t:
        raise ValueError(f'Number of teams must be between {min_t} and {max_t}.')

    if solo_mode:
        num_teams = int(current_app.config.get('SOLO_NUM_TEAMS', 2))
        human_team_id = 1
    else:
        human_team_id = None

    master = load_master_players(mode)
    desired = num_teams * per
    pool_size = min(desired, len(master))
    if seed is None:
        seed = random.randint(1, 2_000_000_000)

    ensure = ALL_STARS_CORE if mode == 'all_stars' else None
    uq = _uncapped_quota(num_teams, mode)
    selected = select_balanced_pool(
        master, pool_size, seed=seed, ensure_names=ensure, uncapped_target=uq,
    )
    pool = order_round1_pool(selected)
    for p in pool:
        p['sold_to'] = None
        p['sold_price'] = None
        p['status'] = 'pending'
        p['auction_round'] = 1

    names = team_names or []
    teams = []
    for i in range(num_teams):
        tid = i + 1
        default_name = f'Team {tid}'
        if solo_mode and tid != human_team_id:
            default_name = f'CPU {tid}'
        name = names[i].strip() if i < len(names) and str(names[i]).strip() else default_name
        is_cpu = bool(solo_mode and tid != human_team_id)
        teams.append(_default_team(tid, name, is_cpu=is_cpu))

    state = {
        'status': 'auction',
        'num_teams': num_teams,
        'teams': teams,
        'auction_pool': pool,
        'sold': [],
        'unsold': [],
        'round1_unsold': [],
        'current_index': 0,
        'current_bid': {
            'amount': 0.0,
            'team_id': None,
            'team_name': None,
        },
        'history': [],
        'round': 1,
        'pool_seed': seed,
        'auction_mode': mode,
        'solo_mode': bool(solo_mode),
        'human_team_id': human_team_id,
        'human_raises_on_current': 0,
    }
    with _lock:
        _save(state)
        return deepcopy(state)


def _current_player(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    idx = state['current_index']
    pool = state['auction_pool']
    if idx < 0 or idx >= len(pool):
        return None
    return pool[idx]


def _team_by_id(state: Dict[str, Any], team_id: int) -> Optional[Dict[str, Any]]:
    for t in state['teams']:
        if t['id'] == team_id:
            return t
    return None


def _slots_left(team: Dict[str, Any]) -> int:
    return current_app.config['MAX_SQUAD_SIZE'] - len(team['players'])


def _needed_for_min(team: Dict[str, Any], after_purchase: bool = False) -> int:
    count = len(team['players']) + (1 if after_purchase else 0)
    return max(0, current_app.config['MIN_SQUAD_SIZE'] - count)


def _remaining_budget(team: Dict[str, Any]) -> float:
    return round(team['budget'] - team['spent'], 2)


def _player_base(player: Dict[str, Any]) -> float:
    if player.get('base_price') is not None:
        return float(player['base_price'])
    return _tier_base_price(player.get('tier') or 'capped')


def _any_team_needs_players(state: Dict[str, Any]) -> bool:
    return any(_slots_left(t) > 0 for t in state['teams'])


def _start_reaution_round(state: Dict[str, Any]) -> bool:
    """Start round 2 from round-1 unsolds (random). Returns True if started."""
    unsolds = deepcopy(state.get('round1_unsold') or [])
    if not unsolds:
        unsolds = [deepcopy(p) for p in state.get('unsold') or []]
    if not unsolds or not _any_team_needs_players(state):
        return False

    rng = random.Random((state.get('pool_seed') or 0) + 99)
    rng.shuffle(unsolds)
    for p in unsolds:
        p['sold_to'] = None
        p['sold_to_name'] = None
        p['sold_price'] = None
        p['status'] = 'pending'
        p['auction_round'] = 2

    state['auction_pool'] = unsolds
    state['current_index'] = 0
    state['current_bid'] = {'amount': 0.0, 'team_id': None, 'team_name': None}
    state['round'] = 2
    state['unsold'] = []  # fresh unsold tracking for round 2 display; history keeps round1
    state.setdefault('history', []).append({
        'player_id': None,
        'player_name': '— Re-auction round —',
        'role': None,
        'team_id': None,
        'team_name': None,
        'price': None,
        'outcome': 'reaution_start',
    })
    return True


def place_bid(
    team_id: int,
    amount: Optional[float] = None,
    *,
    allow_cpu: bool = False,
) -> Dict[str, Any]:
    """Place or raise bid for the current player. amount=None means +increment (or base)."""
    with _lock:
        state = get_state()
        if state['status'] != 'auction':
            raise ValueError('Auction is not in progress.')

        player = _current_player(state)
        if player is None:
            raise ValueError('No player currently up for auction.')

        team = _team_by_id(state, team_id)
        if team is None:
            raise ValueError('Invalid team.')

        if state.get('solo_mode') and team.get('is_cpu') and not allow_cpu:
            raise ValueError(f'{team["name"]} is a CPU team — only you can bid manually.')

        if _slots_left(team) <= 0:
            raise ValueError(
                f'{team["name"]} already has a full squad of '
                f'{current_app.config["MAX_SQUAD_SIZE"]}.'
            )

        base = _player_base(player)
        step = float(current_app.config['BID_INCREMENT'])
        reserve_unit = float(current_app.config['RESERVE_BASE_PRICE'])
        current = state['current_bid'] or {'amount': 0.0, 'team_id': None}

        if amount is None:
            if not current.get('team_id') or current.get('amount', 0) <= 0:
                amount = base
            else:
                amount = round(current['amount'] + step, 2)

        amount = round(float(amount), 2)

        if amount < base:
            raise ValueError(f'Bid must be at least base price {base} Cr.')

        if current.get('team_id') and amount < round(current['amount'] + step, 2):
            raise ValueError(
                f'Minimum raise is {step} Cr '
                f'(need at least {round(current["amount"] + step, 2)} Cr).'
            )

        needed_after = _needed_for_min(team, after_purchase=True)
        reserve = round(needed_after * reserve_unit, 2)
        available = _remaining_budget(team)
        if amount + reserve > available + 1e-9:
            raise ValueError(
                f'{team["name"]} can bid at most {round(available - reserve, 2)} Cr '
                f'(must keep {reserve} Cr for {needed_after} more players to hit min squad).'
            )

        if current.get('team_id') == team_id:
            raise ValueError(f'{team["name"]} already holds the highest bid.')

        state['current_bid'] = {
            'amount': amount,
            'team_id': team_id,
            'team_name': team['name'],
        }
        if state.get('solo_mode') and (not team.get('is_cpu')) and (not allow_cpu):
            state['human_raises_on_current'] = int(state.get('human_raises_on_current') or 0) + 1
        _save(state)
        return _public_state(state)


def _advance(state: Dict[str, Any]) -> None:
    state['current_index'] += 1
    state['current_bid'] = {'amount': 0.0, 'team_id': None, 'team_name': None}
    state['human_raises_on_current'] = 0
    if state['current_index'] < len(state['auction_pool']):
        return

    # End of current pool
    if state.get('round', 1) == 1:
        # Snapshot round-1 unsolds for possible re-auction
        state['round1_unsold'] = deepcopy(state.get('unsold') or [])
        if _start_reaution_round(state):
            return
    state['status'] = 'finished'


def sell_current() -> Dict[str, Any]:
    """Sell current player to highest bidder."""
    with _lock:
        state = get_state()
        if state['status'] != 'auction':
            raise ValueError('Auction is not in progress.')

        player = _current_player(state)
        if player is None:
            raise ValueError('No player currently up for auction.')

        bid = state.get('current_bid') or {}
        if not bid.get('team_id') or bid.get('amount', 0) <= 0:
            raise ValueError('Need at least one bid to sell. Use UNSOLD if nobody bids.')

        team = _team_by_id(state, bid['team_id'])
        if team is None:
            raise ValueError('Bidding team not found.')

        price = round(float(bid['amount']), 2)
        if _slots_left(team) <= 0:
            raise ValueError(f'{team["name"]} has a full squad.')
        if price > _remaining_budget(team) + 1e-9:
            raise ValueError(f'{team["name"]} cannot afford {price} Cr.')

        sold_player = deepcopy(player)
        sold_player['sold_to'] = team['id']
        sold_player['sold_to_name'] = team['name']
        sold_player['sold_price'] = price
        sold_player['status'] = 'sold'

        pool_player = state['auction_pool'][state['current_index']]
        pool_player['sold_to'] = team['id']
        pool_player['sold_to_name'] = team['name']
        pool_player['sold_price'] = price
        pool_player['status'] = 'sold'

        team['players'].append(sold_player)
        team['spent'] = round(team['spent'] + price, 2)

        state.setdefault('sold', []).append(sold_player)
        state.setdefault('history', []).append({
            'player_id': sold_player['id'],
            'player_name': sold_player['name'],
            'role': sold_player['role'],
            'tier': sold_player.get('tier'),
            'team_id': team['id'],
            'team_name': team['name'],
            'price': price,
            'outcome': 'sold',
            'round': state.get('round', 1),
        })

        _advance(state)
        _save(state)
        return _public_state(state)


def unsold_current() -> Dict[str, Any]:
    """Mark current player unsold and move on. Clears any active bid."""
    with _lock:
        state = get_state()
        if state['status'] != 'auction':
            raise ValueError('Auction is not in progress.')

        player = _current_player(state)
        if player is None:
            raise ValueError('No player currently up for auction.')

        unsold_player = deepcopy(player)
        unsold_player['sold_to'] = None
        unsold_player['sold_to_name'] = None
        unsold_player['sold_price'] = None
        unsold_player['status'] = 'unsold'

        pool_player = state['auction_pool'][state['current_index']]
        pool_player['sold_to'] = None
        pool_player['sold_to_name'] = None
        pool_player['sold_price'] = None
        pool_player['status'] = 'unsold'

        state.setdefault('unsold', []).append(unsold_player)
        state.setdefault('history', []).append({
            'player_id': unsold_player['id'],
            'player_name': unsold_player['name'],
            'role': unsold_player['role'],
            'tier': unsold_player.get('tier'),
            'team_id': None,
            'team_name': None,
            'price': None,
            'outcome': 'unsold',
            'round': state.get('round', 1),
        })

        _advance(state)
        _save(state)
        return _public_state(state)


def _auction_pipeline(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Full auction roadmap: categories in order with per-player status for planning."""
    pool = state.get('auction_pool') or []
    idx = int(state.get('current_index') or 0)
    round_no = state.get('round', 1)

    if not pool:
        return []

    if round_no == 2:
        groups_spec = [('reaution', None, 'Re-auction (unsold)')]
        def _match(p, tier, role):
            return True
    else:
        groups_spec = []
        for tier in TIER_ORDER:
            for role in ROLE_ORDER:
                groups_spec.append(
                    (tier, role, f'{TIER_LABELS.get(tier, tier)} · {ROLE_LABELS.get(role, role)}')
                )

        def _match(p, tier, role):
            return p.get('tier') == tier and p.get('role') == role

    pipeline = []
    for tier, role, label in groups_spec:
        items = []
        for i, p in enumerate(pool):
            if not _match(p, tier, role):
                continue
            if i < idx:
                status = p.get('status') or 'sold'
                if status == 'pending':
                    status = 'sold' if any(s.get('id') == p['id'] for s in state.get('sold', [])) else (
                        'unsold' if any(u.get('id') == p['id'] for u in state.get('unsold', [])) else 'gone'
                    )
            elif i == idx and state.get('status') == 'auction':
                status = 'current'
            else:
                status = 'upcoming'
            items.append({
                'id': p['id'],
                'name': p['name'],
                'role': p.get('role'),
                'tier': p.get('tier'),
                'base_price': p.get('base_price'),
                'status': status,
                'sold_to_name': p.get('sold_to_name'),
                'sold_price': p.get('sold_price'),
                'pool_index': i,
            })
        if not items:
            continue
        remaining = [x for x in items if x['status'] in ('current', 'upcoming')]
        is_current = any(x['status'] == 'current' for x in items)
        is_past = all(x['status'] not in ('current', 'upcoming') for x in items)
        pipeline.append({
            'tier': tier,
            'role': role,
            'label': label,
            'tier_label': TIER_LABELS.get(tier, tier) if tier and tier != 'reaution' else None,
            'role_label': ROLE_LABELS.get(role, role) if role else None,
            'total': len(items),
            'remaining_count': len(remaining),
            'upcoming_count': sum(1 for x in items if x['status'] == 'upcoming'),
            'gone_count': sum(1 for x in items if x['status'] not in ('current', 'upcoming')),
            'is_current': is_current,
            'is_past': is_past,
            'players': items,
            'preview_names': [x['name'] for x in remaining[:4]],
        })
    return pipeline


def _category_tracker(state: Dict[str, Any], current_player: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Side tracker for current tier+role (round 1) or whole random pool (round 2)."""
    pool = state.get('auction_pool') or []
    idx = state.get('current_index', 0)
    round_no = state.get('round', 1)

    if not pool:
        return {
            'role': None,
            'label': None,
            'tier': None,
            'tier_label': None,
            'gone': [],
            'current': None,
            'upcoming': [],
            'total': 0,
            'gone_count': 0,
            'upcoming_count': 0,
            'mode': 'empty',
        }

    if round_no == 2:
        # Track entire re-auction list
        scope = list(enumerate(pool))
        label = 'Re-auction (unsold)'
        role = None
        tier = None
    else:
        if current_player:
            role = current_player['role']
            tier = current_player.get('tier')
        else:
            ref = pool[min(idx, len(pool) - 1)]
            role = ref['role']
            tier = ref.get('tier')
        scope = [(i, p) for i, p in enumerate(pool) if p.get('tier') == tier and p['role'] == role]
        label = f'{TIER_LABELS.get(tier, tier)} · {ROLE_LABELS.get(role, role)}'

    gone, upcoming, current = [], [], None
    for p_idx, p in scope:
        entry = {
            'id': p['id'],
            'name': p['name'],
            'role': p['role'],
            'tier': p.get('tier'),
            'base_price': _player_base(p),
            'status': p.get('status', 'pending'),
            'sold_to_name': p.get('sold_to_name'),
            'sold_price': p.get('sold_price'),
            'pool_index': p_idx,
        }
        if p_idx < idx:
            if entry['status'] == 'pending':
                if any(s.get('id') == p['id'] for s in state.get('sold', [])):
                    sold = next(s for s in state['sold'] if s['id'] == p['id'])
                    entry['status'] = 'sold'
                    entry['sold_to_name'] = sold.get('sold_to_name')
                    entry['sold_price'] = sold.get('sold_price')
                elif any(u.get('id') == p['id'] for u in state.get('unsold', [])):
                    entry['status'] = 'unsold'
            gone.append(entry)
        elif p_idx == idx and state.get('status') == 'auction':
            entry['status'] = 'current'
            current = entry
        else:
            entry['status'] = 'upcoming'
            upcoming.append(entry)

    return {
        'role': role if round_no == 1 else None,
        'label': label,
        'tier': tier if round_no == 1 else None,
        'tier_label': TIER_LABELS.get(tier, tier) if tier else ('Re-auction' if round_no == 2 else None),
        'gone': gone,
        'current': current,
        'upcoming': upcoming,
        'total': len(scope),
        'gone_count': len(gone),
        'upcoming_count': len(upcoming),
        'mode': 'reaution' if round_no == 2 else 'tier_role',
    }


def _public_state(state: Dict[str, Any]) -> Dict[str, Any]:
    step = float(current_app.config['BID_INCREMENT'])
    min_squad = current_app.config['MIN_SQUAD_SIZE']
    max_squad = current_app.config['MAX_SQUAD_SIZE']
    reserve_unit = float(current_app.config['RESERVE_BASE_PRICE'])
    player = _current_player(state) if state['status'] == 'auction' else None
    player_base = _player_base(player) if player else float(current_app.config['BASE_PRICE_CAPPED'])

    teams_out = []
    for t in state['teams']:
        remaining = _remaining_budget(t)
        slots = _slots_left(t)
        needed_after = max(0, min_squad - (len(t['players']) + 1)) if slots > 0 else 0
        reserve = round(needed_after * reserve_unit, 2)
        max_bid = round(max(0.0, remaining - reserve), 2) if slots > 0 else 0.0
        teams_out.append({
            **t,
            'is_cpu': bool(t.get('is_cpu')),
            'remaining_budget': remaining,
            'slots_left': slots,
            'players_count': len(t['players']),
            'needed_for_min': max(0, min_squad - len(t['players'])),
            'max_bid': max_bid,
            'can_bid': slots > 0 and max_bid + 1e-9 >= player_base,
        })

    bid = state.get('current_bid') or {}
    next_min = player_base if not bid.get('team_id') else round(bid.get('amount', 0) + step, 2)
    processed = state['current_index']
    tracker = _category_tracker(state, player)
    pipeline = _auction_pipeline(state)

    return {
        'status': state['status'],
        'round': state.get('round', 1),
        'num_teams': state['num_teams'],
        'teams': teams_out,
        'auction_pool_size': len(state['auction_pool']),
        'sold_count': len(state.get('sold', [])),
        'unsold_count': len(state.get('unsold', [])),
        'round1_unsold_count': len(state.get('round1_unsold', [])),
        'processed_count': processed,
        'remaining_count': max(0, len(state['auction_pool']) - processed),
        'current_index': state['current_index'],
        'current_player': player,
        'current_bid': bid,
        'next_min_bid': next_min,
        'base_price': player_base,
        'bid_increment': step,
        'squad_size': max_squad,
        'min_squad_size': min_squad,
        'max_squad_size': max_squad,
        'team_budget': float(current_app.config['TEAM_BUDGET']),
        'history': state.get('history', []),
        'sold': state.get('sold', []),
        'unsold': state.get('unsold', []),
        'auction_pool': state.get('auction_pool', []),
        'category_tracker': tracker,
        'auction_pipeline': pipeline,
        'role_labels': ROLE_LABELS,
        'tier_labels': TIER_LABELS,
        'role_order': ROLE_ORDER,
        'tier_order': TIER_ORDER,
        'pool_seed': state.get('pool_seed'),
        'auction_mode': state.get('auction_mode', 'standard'),
        'solo_mode': bool(state.get('solo_mode')),
        'human_team_id': state.get('human_team_id'),
        'human_raises_on_current': int(state.get('human_raises_on_current') or 0),
        'base_prices': {
            'marquee': float(current_app.config['BASE_PRICE_MARQUEE']),
            'capped': float(current_app.config['BASE_PRICE_CAPPED']),
            'uncapped': float(current_app.config['BASE_PRICE_UNCAPPED']),
        },
    }


def get_public_state() -> Dict[str, Any]:
    with _lock:
        return _public_state(get_state())



def run_cpu_bid_round(max_bids: int = 1) -> Dict[str, Any]:
    """In solo mode, let CPU teams bid/raise until none want to continue."""
    from services.cpu_bots import choose_cpu_bid

    with _lock:
        global _state
        if _state is None:
            get_state()
        state = _state
        if state['status'] != 'auction':
            raise ValueError('Auction is not in progress.')
        if not state.get('solo_mode'):
            raise ValueError('CPU bidding is only available in Solo (You vs CPU) mode.')

        actions: List[Dict[str, Any]] = []
        step = float(current_app.config['BID_INCREMENT'])
        reserve_unit = float(current_app.config['RESERVE_BASE_PRICE'])

        for _ in range(max_bids):
            public = _public_state(state)
            player = public.get('current_player')
            if not player:
                break
            next_min = float(public['next_min_bid'])
            choice = choose_cpu_bid(state, public['teams'], player, next_min, step)
            if not choice:
                break
            team_id, amount = choice
            team = _team_by_id(state, team_id)
            if team is None or not team.get('is_cpu'):
                break
            if _slots_left(team) <= 0:
                break

            base = _player_base(player)
            current = state['current_bid'] or {'amount': 0.0, 'team_id': None}
            amount = round(float(amount), 2)
            if amount < base:
                break
            if current.get('team_id') and amount < round(current['amount'] + step, 2):
                amount = round(current['amount'] + step, 2)
            needed_after = _needed_for_min(team, after_purchase=True)
            reserve = round(needed_after * reserve_unit, 2)
            available = _remaining_budget(team)
            if amount + reserve > available + 1e-9:
                break
            if current.get('team_id') == team_id:
                break

            state['current_bid'] = {
                'amount': amount,
                'team_id': team_id,
                'team_name': team['name'],
            }
            actions.append({
                'team_id': team_id,
                'team_name': team['name'],
                'amount': amount,
                'player_name': player.get('name'),
            })

        cache = state.get('_cpu_ceilings')
        _save(state)
        if cache is not None and _state is not None:
            _state['_cpu_ceilings'] = cache
        out = _public_state(_state)
        out['cpu_actions'] = actions
        return out


def master_players_summary() -> Dict[str, Any]:
    players = load_master_players()
    by_role: Dict[str, List[Dict[str, Any]]] = {}
    by_tier: Dict[str, List[Dict[str, Any]]] = {}
    for p in players:
        by_role.setdefault(p['role'], []).append(p)
        by_tier.setdefault(p['tier'], []).append(p)
    return {
        'total': len(players),
        'players': players,
        'by_role': {
            role: {
                'label': ROLE_LABELS.get(role, role),
                'count': len(items),
                'players': items,
            }
            for role, items in by_role.items()
        },
        'by_tier': {
            tier: {
                'label': TIER_LABELS.get(tier, tier),
                'count': len(items),
                'players': items,
            }
            for tier, items in by_tier.items()
        },
        'counts': _tier_role_counts(players),
        'role_labels': ROLE_LABELS,
        'tier_labels': TIER_LABELS,
        'base_prices': {
            'marquee': float(current_app.config['BASE_PRICE_MARQUEE']),
            'capped': float(current_app.config['BASE_PRICE_CAPPED']),
            'uncapped': float(current_app.config['BASE_PRICE_UNCAPPED']),
        },
        'team_budget': float(current_app.config['TEAM_BUDGET']),
        'squad_size': current_app.config['MAX_SQUAD_SIZE'],
        'min_squad_size': current_app.config['MIN_SQUAD_SIZE'],
        'max_squad_size': current_app.config['MAX_SQUAD_SIZE'],
        'players_per_team_pool': int(current_app.config['PLAYERS_PER_TEAM_POOL']),
        'min_teams': current_app.config['MIN_TEAMS'],
        'max_teams': current_app.config['MAX_TEAMS'],
        'auction_order': (
            'Marquee → Capped → Uncapped; Batter → WK → AR → Bowler; '
            'then one unsold re-auction round if needed'
        ),
    }
