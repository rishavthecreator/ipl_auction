"""About page."""

from flask import Blueprint, render_template

bp = Blueprint('about', __name__)

ABOUT_INFO = {
    "purpose": (
        "Public IPL-style cricket player auction for friends and parties. "
        "Play on one screen (local multiplayer) or create a room code so friends "
        "can join from their own devices. "
        "Player pool is built from an IPL 2025-style roster (balanced tiers & roles). "
        "Single-player vs CPU is still under development."
    ),
    "owner": {
        "name": "IPL Auction",
        "email": "",
        "team": "Community project",
    },
    "data_sources": [
        {
            "name": "players.json",
            "type": "Local JSON",
            "detail": "Auction roster — marquee / capped / uncapped with roles",
        },
        {
            "name": "all_stars.json",
            "type": "Local JSON",
            "detail": "All Stars pool (2 Cr base)",
        },
        {
            "name": "data/rooms/",
            "type": "Room sessions",
            "detail": "Short-lived room codes for remote multiplayer sync",
        },
    ],
    "refresh_cadence": "Live room polling ~1.5s; local party mode uses shared server state",
    "known_limitations": [
        "Single player (You vs CPU) is Developing — disabled on this site",
        "Room codes work on a single server instance (do not horizontally scale without shared storage)",
        "One unsold re-auction round; teams may still finish below max squad size",
    ],
    "links": [],
}


@bp.route('/about')
def index():
    return render_template('pages/about.html', info=ABOUT_INFO)
