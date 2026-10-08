"""About page."""

from flask import Blueprint, render_template

bp = Blueprint('about', __name__)

ABOUT_INFO = {
    "purpose": (
        "Public IPL-style cricket player auction for friends and parties. "
        "Play on one screen (local multiplayer) or create a room code so friends "
        "can join from their own devices. "
        "Pool size is teams × 30 (balanced across marquee / capped / uncapped and roles). "
        "Shuffled pool: uncapped ≥ teams × 5, Indians ≥ teams × 12; bid rule max 8 overseas per team. "
        "All Stars: Classic, 2014, 2018, or 2022; "
        "top 25 @ 2 Cr, others @ 1 Cr. "
        "Round 1 order: Marquee → Capped → Uncapped; within each Batter → WK → AR → Bowler. "
        "Base prices (shuffled): Marquee 2 Cr, Capped 1 Cr, Uncapped 0.5 Cr. "
        "If teams are short of 20 after round 1, unsold players get one random-order re-auction. "
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
            "type": "Local JSON (IPL 2025 auction list)",
            "detail": "Curated IPL 2025-style roster — marquee / capped / uncapped + specialism (~574 players)",
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
        "Budget reserve: 0.5 Cr × remaining players needed to hit the 15 minimum",
        "One unsold re-auction round; teams may still finish below max squad size",
    ],
    "links": [],
}


@bp.route('/about')
def index():
    return render_template('pages/about.html', info=ABOUT_INFO)
