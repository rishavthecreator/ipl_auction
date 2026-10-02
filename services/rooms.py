"""Room codes + live sync for remote multiplayer."""

from __future__ import annotations

import json
import os
import random
import re
import string
import threading
from copy import deepcopy
from typing import Any, Callable, Dict, Optional

from flask import current_app

from services import auction as auction_svc

_room_lock = threading.RLock()
_CODE_RE = re.compile(r'^[A-Z0-9]{4,8}$')


def _rooms_dir() -> str:
    d = current_app.config['ROOMS_FOLDER']
    os.makedirs(d, exist_ok=True)
    return d


def _path(code: str) -> str:
    return os.path.join(_rooms_dir(), f'{code}.json')


def normalize_code(code: str) -> str:
    c = (code or '').strip().upper().replace(' ', '').replace('-', '')
    if not _CODE_RE.match(c):
        raise ValueError('Room code must be 4–8 letters/numbers.')
    return c


def _new_code() -> str:
    alphabet = (string.ascii_uppercase + string.digits).replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    for _ in range(50):
        code = ''.join(random.choice(alphabet) for _ in range(5))
        if not os.path.exists(_path(code)):
            return code
    raise ValueError('Could not allocate a room code. Try again.')


def save_room(code: str, state: Dict[str, Any]) -> None:
    code = normalize_code(code)
    path = _path(code)
    tmp = path + '.tmp'
    payload = deepcopy(state)
    payload.pop('_cpu_ceilings', None)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_room(code: str) -> Dict[str, Any]:
    code = normalize_code(code)
    path = _path(code)
    if not os.path.exists(path):
        raise ValueError(f'Room {code} not found.')
    with open(path, 'r', encoding='utf-8') as f:
        state = json.load(f)
    state.setdefault('room', {})
    state['room'].setdefault('claims', {})
    state['room'].setdefault('clients', {})
    state['room']['code'] = code
    return state


def create_room(host_client_id: str, host_name: str = 'Host') -> Dict[str, Any]:
    if not (host_client_id or '').strip():
        raise ValueError('client_id required')
    with _room_lock:
        code = _new_code()
        state = auction_svc._empty_state()
        state['room'] = {
            'code': code,
            'host_client_id': host_client_id,
            'host_name': (host_name or 'Host').strip() or 'Host',
            'claims': {},
            'clients': {
                host_client_id: {
                    'name': (host_name or 'Host').strip() or 'Host',
                    'role': 'host',
                },
            },
        }
        save_room(code, state)
        return public_room(state, host_client_id)


def join_room(code: str, client_id: str, name: str = '') -> Dict[str, Any]:
    if not (client_id or '').strip():
        raise ValueError('client_id required')
    code = normalize_code(code)
    with _room_lock:
        state = load_room(code)
        clients = state['room'].setdefault('clients', {})
        display = (name or '').strip() or f'Player {len(clients) + 1}'
        if client_id not in clients:
            role = 'host' if client_id == state['room'].get('host_client_id') else 'player'
            clients[client_id] = {'name': display, 'role': role}
        elif name.strip():
            clients[client_id]['name'] = name.strip()
        save_room(code, state)
        return public_room(state, client_id)


def claim_team(code: str, client_id: str, team_id: int) -> Dict[str, Any]:
    code = normalize_code(code)
    with _room_lock:
        state = load_room(code)
        teams = state.get('teams') or []
        if not teams:
            raise ValueError('Host has not started the auction / set teams yet.')
        team = next((t for t in teams if t['id'] == int(team_id)), None)
        if team is None:
            raise ValueError('Invalid team.')
        claims = state['room'].setdefault('claims', {})
        for tid, claim in list(claims.items()):
            if claim.get('client_id') == client_id:
                del claims[tid]
        key = str(int(team_id))
        existing = claims.get(key)
        if existing and existing.get('client_id') != client_id:
            raise ValueError(f'Team already claimed by {existing.get("name")}.')
        client = (state['room'].get('clients') or {}).get(client_id) or {}
        claims[key] = {
            'client_id': client_id,
            'name': client.get('name') or 'Player',
        }
        save_room(code, state)
        return public_room(state, client_id)


def public_room(state: Dict[str, Any], client_id: str = '') -> Dict[str, Any]:
    with auction_svc._lock:
        old = auction_svc._state
        auction_svc._state = deepcopy(state)
        try:
            pub = auction_svc._public_state(auction_svc._state)
        finally:
            auction_svc._state = old

    room = state.get('room') or {}
    claims = room.get('claims') or {}
    my_team = None
    for tid, c in claims.items():
        if c.get('client_id') == client_id:
            my_team = int(tid)
            break
    is_host = bool(client_id) and client_id == room.get('host_client_id')
    pub['room'] = {
        'code': room.get('code'),
        'host_name': room.get('host_name'),
        'is_host': is_host,
        'my_team_id': my_team,
        'claims': {
            int(tid): {'name': c.get('name'), 'client_id': c.get('client_id')}
            for tid, c in claims.items()
        },
        'client_count': len(room.get('clients') or {}),
    }
    pub['room_mode'] = True
    return pub


def get_room_state(code: str, client_id: str = '') -> Dict[str, Any]:
    with _room_lock:
        return public_room(load_room(code), client_id)


def configure_and_start(
    code: str,
    host_client_id: str,
    num_teams: int,
    team_names,
    seed,
    mode: str,
) -> Dict[str, Any]:
    code = normalize_code(code)
    with _room_lock:
        state = load_room(code)
        if state['room'].get('host_client_id') != host_client_id:
            raise ValueError('Only the host can start the auction.')
        room_meta = deepcopy(state['room'])

        with auction_svc._lock:
            old = auction_svc._state
            try:
                auction_svc.start_auction(
                    num_teams,
                    team_names,
                    seed=seed,
                    mode=mode or 'standard',
                    solo_mode=False,
                )
                new_state = deepcopy(auction_svc._state)
            finally:
                auction_svc._state = old

        new_state['room'] = room_meta
        claims = new_state['room'].setdefault('claims', {})
        claims['1'] = {
            'client_id': host_client_id,
            'name': room_meta.get('host_name') or 'Host',
        }
        save_room(code, new_state)
        return public_room(new_state, host_client_id)


def run_in_room(code: str, fn: Callable[[], Any]) -> Any:
    """Run an auction mutation against a room's state, then persist."""
    code = normalize_code(code)
    with _room_lock:
        with auction_svc._lock:
            old = auction_svc._state
            state = load_room(code)
            room_meta = deepcopy(state.get('room') or {})
            auction_svc._state = state
            try:
                result = fn()
                fresh = deepcopy(auction_svc._state or state)
                fresh['room'] = room_meta
                # If result is public state dict, re-wrap with room after save
                save_room(code, fresh)
                return result, fresh
            finally:
                auction_svc._state = old


def assert_can_bid(state: Dict[str, Any], client_id: str, team_id: int) -> None:
    claims = (state.get('room') or {}).get('claims') or {}
    claim = claims.get(str(int(team_id)))
    if not claim or claim.get('client_id') != client_id:
        raise ValueError('You can only bid for the team you claimed.')


def assert_host(state: Dict[str, Any], client_id: str) -> None:
    if (state.get('room') or {}).get('host_client_id') != client_id:
        raise ValueError('Only the host can do that.')
