"""JSON API for auction actions."""

from flask import Blueprint, current_app, jsonify, request

from services import auction as auction_svc
from services import rooms as rooms_svc

bp = Blueprint('api', __name__, url_prefix='/api')


def _error(msg, code=400):
    return jsonify({'ok': False, 'error': msg}), code


def _client_id():
    payload = request.get_json(silent=True) or {}
    return (
        payload.get('client_id')
        or request.args.get('client_id')
        or request.headers.get('X-Client-Id')
        or ''
    ).strip()


def _room_code():
    payload = request.get_json(silent=True) or {}
    return (payload.get('room_code') or request.args.get('room_code') or '').strip()


@bp.route('/players')
def players():
    return jsonify({'ok': True, **auction_svc.master_players_summary()})


@bp.route('/all-stars-auctions')
def all_stars_auctions():
    return jsonify({'ok': True, **auction_svc.list_all_stars_auctions()})


@bp.route('/preview-pool')
def preview_pool():
    try:
        num_teams = int(request.args.get('num_teams', 0) or 0)
        seed = request.args.get('seed')
        seed_i = int(seed) if seed not in (None, '') else None
        mode = request.args.get('mode') or 'standard'
        auction_year = request.args.get('auction_year')
        return jsonify({
            'ok': True,
            **auction_svc.preview_pool(
                num_teams, seed=seed_i, mode=mode, auction_year=auction_year,
            ),
        })
    except (TypeError, ValueError) as e:
        return _error(str(e))


@bp.route('/state')
def state():
    code = _room_code()
    if code:
        try:
            return jsonify({
                'ok': True,
                'state': rooms_svc.get_room_state(code, _client_id()),
            })
        except ValueError as e:
            return _error(str(e))
    return jsonify({'ok': True, 'state': auction_svc.get_public_state()})


@bp.route('/rooms/create', methods=['POST'])
def rooms_create():
    payload = request.get_json(silent=True) or {}
    try:
        pub = rooms_svc.create_room(
            host_client_id=_client_id() or payload.get('client_id') or '',
            host_name=payload.get('name') or 'Host',
        )
        return jsonify({'ok': True, 'state': pub, 'room_code': pub['room']['code']})
    except ValueError as e:
        return _error(str(e))


@bp.route('/rooms/join', methods=['POST'])
def rooms_join():
    payload = request.get_json(silent=True) or {}
    try:
        pub = rooms_svc.join_room(
            code=payload.get('room_code') or '',
            client_id=_client_id(),
            name=payload.get('name') or '',
        )
        return jsonify({'ok': True, 'state': pub, 'room_code': pub['room']['code']})
    except ValueError as e:
        return _error(str(e))


@bp.route('/rooms/claim', methods=['POST'])
def rooms_claim():
    payload = request.get_json(silent=True) or {}
    try:
        pub = rooms_svc.claim_team(
            code=payload.get('room_code') or '',
            client_id=_client_id(),
            team_id=int(payload['team_id']),
        )
        return jsonify({'ok': True, 'state': pub})
    except (KeyError, TypeError, ValueError) as e:
        return _error(str(e))


@bp.route('/rooms/start', methods=['POST'])
def rooms_start():
    payload = request.get_json(silent=True) or {}
    try:
        seed = payload.get('seed')
        seed_i = int(seed) if seed not in (None, '') else None
        pub = rooms_svc.configure_and_start(
            code=payload.get('room_code') or '',
            host_client_id=_client_id(),
            num_teams=int(payload.get('num_teams', 0)),
            team_names=payload.get('team_names') or [],
            seed=seed_i,
            mode=payload.get('mode') or 'standard',
            auction_year=payload.get('auction_year'),
        )
        return jsonify({'ok': True, 'state': pub})
    except (TypeError, ValueError) as e:
        return _error(str(e))


@bp.route('/start', methods=['POST'])
def start():
    payload = request.get_json(silent=True) or {}
    try:
        num_teams = int(payload.get('num_teams', 0))
        team_names = payload.get('team_names') or []
        seed = payload.get('seed')
        seed_i = int(seed) if seed not in (None, '') else None
        mode = payload.get('mode') or 'standard'
        solo_mode = bool(payload.get('solo_mode'))
        if solo_mode and not current_app.config.get('SOLO_MODE_ENABLED', False):
            raise ValueError('Single player is still developing — use Multiplayer (local) or a room code.')
        human_team_id = payload.get('human_team_id')
        if human_team_id is not None:
            human_team_id = int(human_team_id)
        auction_svc.start_auction(
            num_teams,
            team_names,
            seed=seed_i,
            mode=mode,
            solo_mode=solo_mode,
            human_team_id=human_team_id,
            auction_year=payload.get('auction_year'),
        )
        return jsonify({'ok': True, 'state': auction_svc.get_public_state()})
    except (TypeError, ValueError) as e:
        return _error(str(e))


@bp.route('/bid', methods=['POST'])
def bid():
    payload = request.get_json(silent=True) or {}
    code = _room_code()
    try:
        team_id = int(payload['team_id'])
        amount = payload.get('amount')
        if amount is not None:
            amount = float(amount)
        if code:
            client_id = _client_id()

            def _do():
                st = auction_svc._state
                rooms_svc.assert_can_bid(st, client_id, team_id)
                return auction_svc.place_bid(team_id, amount)

            result, fresh = rooms_svc.run_in_room(code, _do)
            return jsonify({'ok': True, 'state': rooms_svc.public_room(fresh, client_id)})
        state = auction_svc.place_bid(team_id, amount)
        return jsonify({'ok': True, 'state': state})
    except KeyError:
        return _error('team_id is required')
    except (TypeError, ValueError) as e:
        return _error(str(e))


@bp.route('/sell', methods=['POST'])
def sell():
    code = _room_code()
    try:
        if code:
            client_id = _client_id()

            def _do():
                rooms_svc.assert_host(auction_svc._state, client_id)
                return auction_svc.sell_current()

            _, fresh = rooms_svc.run_in_room(code, _do)
            return jsonify({'ok': True, 'state': rooms_svc.public_room(fresh, client_id)})
        state = auction_svc.sell_current()
        return jsonify({'ok': True, 'state': state})
    except ValueError as e:
        return _error(str(e))


@bp.route('/unsold', methods=['POST'])
def unsold():
    code = _room_code()
    try:
        if code:
            client_id = _client_id()

            def _do():
                rooms_svc.assert_host(auction_svc._state, client_id)
                return auction_svc.unsold_current()

            _, fresh = rooms_svc.run_in_room(code, _do)
            return jsonify({'ok': True, 'state': rooms_svc.public_room(fresh, client_id)})
        state = auction_svc.unsold_current()
        return jsonify({'ok': True, 'state': state})
    except ValueError as e:
        return _error(str(e))


@bp.route('/cpu-round', methods=['POST'])
def cpu_round():
    try:
        state = auction_svc.run_cpu_bid_round()
        return jsonify({'ok': True, 'state': state, 'cpu_actions': state.get('cpu_actions') or []})
    except ValueError as e:
        return _error(str(e))


@bp.route('/reset', methods=['POST'])
def reset():
    code = _room_code()
    if code:
        # Host-only soft reset not implemented — leave room file; local reset only
        return _error('Reset a room from setup by creating a new room code.')
    auction_svc.reset_auction()
    return jsonify({'ok': True, 'state': auction_svc.get_public_state()})
