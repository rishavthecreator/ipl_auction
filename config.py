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
    STATE_FILE = os.path.join(BASE_DIR, 'data', 'auction_state.json')
    ROOMS_FOLDER = os.path.join(BASE_DIR, 'data', 'rooms')

    TEAM_BUDGET = 100.0
    BID_INCREMENT = 0.5
    MIN_SQUAD_SIZE = 15
    MAX_SQUAD_SIZE = 20
    SQUAD_SIZE = 20
    PLAYERS_PER_TEAM_POOL = 30
    UNCAPPED_PER_TEAM = 5
    SOLO_NUM_TEAMS = 2
    SOLO_MODE_ENABLED = False  # CPU solo still developing

    CPU_MARQUEE_ELITE_MAX = 20.0
    CPU_MARQUEE_ELITE_COUNT = 2
    CPU_MARQUEE_NORMAL_MAX = 15.0
    CPU_MARQUEE_NORMAL_MIN = 10.0
    CPU_HUMAN_RAISES_BEFORE_YIELD = 3
    MIN_TEAMS = 2
    MAX_TEAMS = 8

    BASE_PRICE_MARQUEE = 2.0
    BASE_PRICE_CAPPED = 1.0
    BASE_PRICE_UNCAPPED = 0.5
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
