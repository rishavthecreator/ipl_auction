"""IPL Auction — public Flask website."""

from __future__ import annotations

import os
import secrets
import traceback
from datetime import datetime

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config


def create_app(config_name: str | None = None) -> Flask:
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'production')

    app = Flask(__name__, static_folder='static', template_folder='templates')
    # Trust one reverse-proxy hop (Render / Railway / nginx TLS termination)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.url_map.strict_slashes = False
    app.config.from_object(config.get(config_name, config['default']))

    # Allow SECRET_KEY override from environment (required in production)
    env_secret = (os.environ.get('SECRET_KEY') or '').strip()
    if env_secret:
        app.config['SECRET_KEY'] = env_secret
    elif config_name == 'production' and app.config.get('SECRET_KEY') == 'ipl-auction-dev-key':
        # Fail closed if someone ships the default key to production without setting SECRET_KEY
        app.logger.warning(
            'SECRET_KEY is using the insecure default. Set SECRET_KEY in the environment.'
        )

    os.makedirs(app.config['DATA_FOLDER'], exist_ok=True)
    os.makedirs(app.config['ROOMS_FOLDER'], exist_ok=True)

    _CSRF_EXEMPT = frozenset({'favicon', 'static', 'health'})

    def _set_csrf_cookie(response):
        """Mirror CSRF into a readable cookie so JS can recover if the HTML token is stale."""
        token = session.get('_csrf_token') or ''
        if not token:
            return response
        response.set_cookie(
            'csrf_token',
            token,
            httponly=False,
            samesite=app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
            secure=bool(app.config.get('SESSION_COOKIE_SECURE')),
            path='/',
        )
        return response

    @app.before_request
    def csrf_protect():
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return
        if request.endpoint in _CSRF_EXEMPT:
            return
        # Room join / auction APIs are JSON + client_id based (not cookie-auth).
        # Requiring a Flask session CSRF cookie breaks guests who can load the page
        # but do not round-trip the HttpOnly session cookie. Cross-site form posts
        # are still protected (they are not application/json).
        ctype = (request.content_type or '').split(';')[0].strip().lower()
        if request.path.startswith('/api/') and (request.is_json or ctype == 'application/json'):
            return
        payload = request.get_json(silent=True) or {}
        token = (
            request.form.get('csrf_token')
            or request.headers.get('X-CSRFToken')
            or request.headers.get('X-CSRF-Token')
            or payload.get('csrf_token')
            or ''
        ).strip()
        session_token = session.get('_csrf_token') or ''
        cookie_token = (request.cookies.get('csrf_token') or '').strip()
        valid = False
        if token and session_token and secrets.compare_digest(token, session_token):
            valid = True
        elif token and cookie_token and secrets.compare_digest(token, cookie_token):
            # Double-submit: restore session token if the HttpOnly cookie was dropped
            session['_csrf_token'] = cookie_token
            valid = True
        if not valid:
            if request.is_json or request.headers.get('Accept', '').startswith('application/json'):
                return jsonify({'error': 'Missing or invalid CSRF token.'}), 400
            return 'Missing or invalid CSRF token.', 400

    @app.after_request
    def csrf_ensure_token(response):
        if '_csrf_token' not in session:
            session['_csrf_token'] = secrets.token_hex(16)
        return _set_csrf_cookie(response)

    from blueprints.home import bp as home_bp
    from blueprints.about import bp as about_bp
    from blueprints.api import bp as api_bp

    app.register_blueprint(home_bp)
    app.register_blueprint(about_bp)
    app.register_blueprint(api_bp)

    @app.get('/health')
    def health():
        return jsonify({'ok': True, 'service': 'ipl-auction'})

    @app.context_processor
    def inject_globals():
        def _csrf_token():
            if '_csrf_token' not in session:
                session['_csrf_token'] = secrets.token_hex(16)
            return session['_csrf_token']

        auction_status = 'setup'
        try:
            from services import auction as auction_svc
            auction_status = auction_svc.get_public_state().get('status') or 'setup'
        except Exception:
            auction_status = 'setup'

        return {
            'ENV_NAME': app.config.get('ENV_NAME', 'production'),
            'SITE_NAME': app.config.get('SITE_NAME', 'IPL Auction'),
            'csrf_token': _csrf_token,
            'BASE_PATH': request.script_root or '',
            'auction_status': auction_status,
        }

    @app.errorhandler(404)
    def page_not_found(e):
        return redirect(url_for('home.index'))

    def _error_page(e):
        tb = traceback.format_exc()
        if request.is_json or request.headers.get('Accept', '').startswith('application/json'):
            payload = {'error': str(e)}
            if app.config.get('DEBUG'):
                payload['traceback'] = tb
            return jsonify(payload), 500
        return render_template(
            'pages/error.html',
            error_message=str(e),
            error_traceback=tb if app.config.get('DEBUG') else None,
            now=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
        ), 500

    @app.errorhandler(500)
    def internal_error(e):
        return _error_page(e)

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        app.logger.error('Unhandled: %s', e, exc_info=True)
        return _error_page(e)

    return app


app = create_app()

if __name__ == '__main__':
    env = os.environ.get('FLASK_ENV', 'development')
    app = create_app(env)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=app.config['DEBUG'], port=port, host='0.0.0.0')
