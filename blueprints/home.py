"""Home / auction pages (public multiplayer site)."""

from flask import Blueprint, redirect, render_template, request, url_for

from services import auction as auction_svc

bp = Blueprint('home', __name__)


@bp.route('/')
def index():
    # Public site always lands on setup (solo/local state must not hijack room players).
    return render_template('pages/setup.html')


@bp.route('/auction')
def auction_floor():
    room = (request.args.get('room') or '').strip()
    if room:
        return render_template('pages/auction.html')
    state = auction_svc.get_public_state()
    if state['status'] == 'setup':
        return redirect(url_for('home.index'))
    if state['status'] == 'finished':
        return redirect(url_for('home.results'))
    return render_template('pages/auction.html')


@bp.route('/results')
def results():
    room = (request.args.get('room') or '').strip()
    if room:
        return render_template('pages/results.html')
    state = auction_svc.get_public_state()
    if state['status'] != 'finished':
        if state['status'] == 'auction':
            return redirect(url_for('home.auction_floor'))
        return redirect(url_for('home.index'))
    return render_template('pages/results.html')
