"""Flask configuration for IPL Auction (public website)."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    ENV_NAME = 'production'
    SITE_NAME = 'IPL Auction'
    SECRET_KEY = os.environ.get('SECRET_KEY', 'ipl-auction-dev-key')
    DEBUG = False

    # Secure cookies when behind HTTPS (Render/Railway set this via ProxyFix + X-Forwarded-Proto)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', '1') not in ('0', 'false', 'False')

    BASE_DIR = BASE_DIR
    DATA_FOLDER = os.path.join(BASE_DIR, 'data')
    STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
    PLAYERS_FILE = os.path.join(BASE_DIR, 'data', 'players.json')
    ALL_STARS_FILE = os.path.join(BASE_DIR, 'data', 'all_stars.json')
    ALL_STARS_AUCTIONS_FILE = os.path.join(BASE_DIR, 'data', 'all_stars_auctions.json')
    STATE_FILE = os.path.join(BASE_DIR, 'data', 'auction_state.json')
    ROOMS_FOLDER = os.path.join(BASE_DIR, 'data', 'rooms')

    # All Stars pricing: top 25 → marquee (2 Cr), others → capped (1 Cr)
    ALL_STARS_TOP_BASE = 2.0
    ALL_STARS_REST_BASE = 1.0
    ALL_STARS_TOP_COUNT = 25
    ALL_STARS_DEFAULT_YEAR = 'classic'

    TEAM_BUDGET = 100.0
    BID_INCREMENT = 0.5
    MIN_SQUAD_SIZE = 15
    MAX_SQUAD_SIZE = 20
    SQUAD_SIZE = 20  # alias for max
    PLAYERS_PER_TEAM_POOL = 30  # pool = num_teams * 30
    UNCAPPED_PER_TEAM = 5       # base uncapped slots in shuffled pool (may rise for Indian quota)
    MIN_INDIAN_PER_TEAM = 12    # at least teams × 12 Indian players in shuffled pool
    OVERSEAS_PER_TEAM = 8       # max overseas a team may buy (bid rule)
    SOLO_NUM_TEAMS = 2
    SOLO_MODE_ENABLED = False  # CPU solo still developing

    # CPU bidding: fair-value bands (Cr) and behaviour (used when solo is enabled)
    CPU_VALUE_MARQUEE_MIN = 10.0
    CPU_VALUE_MARQUEE_MAX = 14.0
    CPU_VALUE_CAPPED_MIN = 2.0
    CPU_VALUE_CAPPED_MAX = 5.0
    CPU_VALUE_UNCAPPED_MIN = 0.5
    CPU_VALUE_UNCAPPED_MAX = 1.5
    CPU_AGGRESSION_MIN = 0.85
    CPU_AGGRESSION_MAX = 1.15
    CPU_WALK_MULT = 1.15
    CPU_ROLE_NEED_MULT = 1.2
    CPU_ROLE_FULL_MULT = 0.7
    CPU_FIRST_MARQUEE_PACE_FLOOR = 10.0
    CPU_LATE_FILL_MAX = 1.0
    CPU_SIT_OUT_P = 0.05
    MIN_TEAMS = 2
    MAX_TEAMS = 8

    # Game base prices by auction tier (Cr)
    BASE_PRICE_MARQUEE = 2.0
    BASE_PRICE_CAPPED = 1.0
    BASE_PRICE_UNCAPPED = 0.5
    # Conservative reserve per empty min-slot (cheapest possible buy)
    RESERVE_BASE_PRICE = 0.5


class DevelopmentConfig(Config):
    ENV_NAME = 'development'
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    ENV_NAME = 'production'
    DEBUG = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
