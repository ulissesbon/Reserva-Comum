"""Contas locais, senhas scrypt e sessões revogáveis persistidas no SQLite."""
import hashlib
import hmac
import re
import secrets
import time

ROLES = ('usuario', 'gestor', 'admin')
SESSION_SECONDS = 8 * 60 * 60


def timestamp():
    return int(time.time())


def normalize_username(value):
    if not isinstance(value, str):
        raise ValueError('Informe o nome de usuário.')
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9_][a-z0-9_.-]{2,31}', value):
        raise ValueError('Usuário: 3 a 32 letras sem acentos, números, ponto, hífen ou sublinhado.')
    return value


def hash_password(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise ValueError('A senha deve ter entre 10 e 128 caracteres.')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=5, dklen=64)
    return f'scrypt$16384$8$5${salt.hex()}${digest.hex()}'


def verify_password(password, encoded):
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        algorithm, n, r, p, salt, expected = encoded.split('$')
        if (algorithm, n, r, p) != ('scrypt', '16384', '8', '5'):
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                n=int(n), r=int(r), p=int(p), dklen=64)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def init_schema(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('usuario','gestor','admin')),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
            csrf_token TEXT NOT NULL, expires INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_username ON sessions(username);
        CREATE TABLE IF NOT EXISTS login_failures (
            username TEXT NOT NULL, address TEXT NOT NULL, created INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS login_failures_created ON login_failures(created);
        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
            subject TEXT NOT NULL, created INTEGER NOT NULL
        );
    ''')


def security_event(c, actor, action, subject):
    c.execute('INSERT INTO security_events(actor,action,subject,created) VALUES(?,?,?,?)',
              (actor, action, subject, timestamp()))


def create_user(c, username, password, role, created, actor):
    username = normalize_username(username)
    if role not in ROLES:
        raise ValueError('Perfil deve ser usuario, gestor ou admin.')
    encoded = hash_password(password)
    c.execute('INSERT INTO users(username,password_hash,role,created) VALUES(?,?,?,?)',
              (username, encoded, role, created))
    security_event(c, actor, 'conta_criada', username)
    return username


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_identity(c, token):
    if not token or len(token) > 128:
        return None
    row = c.execute('''SELECT u.username, u.role, s.csrf_token FROM sessions s
        JOIN users u ON u.username=s.username
        WHERE s.token_hash=? AND s.expires>? AND u.active=1''',
        (token_hash(token), timestamp())).fetchone()
    return dict(row) if row else None


def login(c, username, password, address, previous_token=None):
    try:
        username = normalize_username(username)
    except ValueError:
        username = '<invalido>'
    clock = timestamp()
    c.execute('DELETE FROM login_failures WHERE created<=?', (clock - 900,))
    failures = c.execute('''SELECT count(*) FROM login_failures
        WHERE username=? OR address=?''', (username, address)).fetchone()[0]
    account_failures = c.execute('SELECT count(*) FROM login_failures WHERE username=?',
                                (username,)).fetchone()[0]
    if account_failures >= 5 or failures >= 50:
        return 429, {'error': 'Muitas tentativas. Tente novamente em 15 minutos.'}, None
    user = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    valid = verify_password(password, user['password_hash'] if user else DUMMY_HASH)
    if not valid or not user or not user['active']:
        c.execute('INSERT INTO login_failures VALUES(?,?,?)', (username, address, clock))
        security_event(c, username, 'login_recusado', username)
        return 401, {'error': 'Usuário ou senha inválidos.'}, None
    c.execute('DELETE FROM login_failures WHERE username=?', (username,))
    c.execute('DELETE FROM sessions WHERE expires<=?', (clock,))
    if previous_token:
        c.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(previous_token),))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions VALUES(?,?,?,?)',
              (token_hash(token), username, csrf, clock + SESSION_SECONDS))
    security_event(c, username, 'login', username)
    return 200, {'ok': True, 'username': username, 'role': user['role'], 'csrf_token': csrf}, token
