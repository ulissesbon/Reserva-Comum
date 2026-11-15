"""Reserva Comum: servidor local Python + SQLite (biblioteca padrão)."""
import json, sqlite3, os, hmac
import argparse
import getpass
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit, parse_qs
import auth
import reports

ROOT=Path(__file__).parent
DB=Path(os.environ.get('RESERVA_DB', str(ROOT/'reservas.db')))
TZ=timezone(timedelta(hours=-3))
def now(): return datetime.now(TZ).replace(tzinfo=None)
class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try: return super().__exit__(*args)
        finally: self.close()
def connect():
    c=sqlite3.connect(DB, timeout=10, factory=ClosingConnection); c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); return c

def init():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS spaces(id INTEGER PRIMARY KEY, name TEXT, description TEXT, capacity INTEGER, opens INTEGER, closes INTEGER, fee REAL, notice INTEGER, monthly_limit INTEGER, approval INTEGER, rules_version TEXT);
        CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, user TEXT, space_id INTEGER REFERENCES spaces(id), start TEXT, end TEXT, purpose TEXT, guests INTEGER, status TEXT, checks TEXT, rules_version TEXT);
        CREATE TABLE IF NOT EXISTS blocks(id INTEGER PRIMARY KEY, space_id INTEGER, start TEXT, end TEXT, reason TEXT);
        CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, space_id INTEGER, user TEXT, description TEXT, action TEXT, penalty_until TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, booking_id INTEGER, actor TEXT, action TEXT, reason TEXT, created TEXT);
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Histórico somente anexável'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Histórico somente anexável'); END;
        CREATE TABLE IF NOT EXISTS block_audit(id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL REFERENCES blocks(id), actor TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS block_audit_no_update BEFORE UPDATE ON block_audit BEGIN SELECT RAISE(ABORT,'Histórico somente anexável'); END;
        CREATE TRIGGER IF NOT EXISTS block_audit_no_delete BEFORE DELETE ON block_audit BEGIN SELECT RAISE(ABORT,'Histórico somente anexável'); END;
        ''')
        if 'removed_at' not in {row['name'] for row in c.execute('PRAGMA table_info(blocks)')}:
            c.execute('ALTER TABLE blocks ADD COLUMN removed_at TEXT')
        if 'created' not in {row['name'] for row in c.execute('PRAGMA table_info(incidents)')}:
            c.execute('ALTER TABLE incidents ADD COLUMN created TEXT')
        auth.init_schema(c)
        if not c.execute('SELECT 1 FROM spaces').fetchone():
            c.executemany('INSERT INTO spaces VALUES(?,?,?,?,?,?,?,?,?,?,?)',[(1,'Churrasqueira','Encontros e celebrações no jardim.',20,8,22,50,24,3,1,'1.0'),(2,'Piscina','Lazer e convivência ao ar livre.',12,8,20,0,2,4,0,'1.0')])
            day=(now()+timedelta(days=3)).date().isoformat()
            c.execute('INSERT INTO blocks(space_id,start,end,reason) VALUES(2,?,?,?)',(day+'T10:00',day+'T12:00','Limpeza programada'))
            c.execute("INSERT INTO bookings(user,space_id,start,end,purpose,guests,status,checks,rules_version) VALUES('outro',1,?,?,'Confraternização',10,'confirmada','[]','1.0')",(day+'T14:00',day+'T16:00'))
            c.execute("INSERT INTO incidents(space_id,user,description,action,penalty_until,created) VALUES(1,'morador','Atraso na limpeza na utilização anterior.','Orientação registrada; sem penalidade.',NULL,?)", (now().isoformat(timespec='seconds'),))
            c.execute('INSERT INTO incidents(space_id,user,description,action,penalty_until,created) VALUES(2,?,?,?,?,?)',('restrito','Descumprimento das regras de uso.','Suspensão temporária.',(now()+timedelta(days=30)).isoformat(timespec='minutes'),now().isoformat(timespec='seconds')))

def validate(c,d,user,exclude=None):
    if type(d['space_id']) is not int: raise ValueError('Espaço deve ser um identificador inteiro.')
    s=c.execute('SELECT * FROM spaces WHERE id=?',(d['space_id'],)).fetchone()
    if not s: raise ValueError('Espaço inexistente.')
    start=datetime.fromisoformat(d['start']); end=datetime.fromisoformat(d['end'])
    if start.tzinfo or end.tzinfo: raise ValueError('Use data e hora local sem fuso.')
    if type(d['guests']) is not int: raise ValueError('Convidados deve ser inteiro.')
    checks=[]
    def check(name,ok,detail): checks.append({'name':name,'ok':bool(ok),'detail':detail})
    check('Intervalo',end>start and start.date()==end.date(),'Fim após início, no mesmo dia.')
    check('Finalidade',isinstance(d.get('purpose'),str) and 3<=len(d['purpose'].strip())<=200,'Descreva a finalidade em 3 a 200 caracteres.')
    check('Termos',d.get('terms') is True,'Aceite os termos antes de solicitar.')
    check('Capacidade',1<=d['guests']<=s['capacity'],f"Informe entre 1 e {s['capacity']} pessoas, incluindo você.")
    check('Antecedência',start>=now()+timedelta(hours=s['notice']),f"Reserve com pelo menos {s['notice']} horas de antecedência.")
    check('Funcionamento',start.hour>=s['opens'] and end<=start.replace(hour=s['closes'],minute=0,second=0,microsecond=0),f"Escolha um intervalo entre {s['opens']}h e {s['closes']}h.")
    overlap=c.execute("SELECT 1 FROM bookings WHERE space_id=? AND status IN ('pendente','confirmada') AND start<? AND end>? AND id!=?",(s['id'],d['end'],d['start'],exclude or -1)).fetchone()
    check('Sobreposição',not overlap,'Escolha outro horário se houver pedido pendente ou reserva confirmada.')
    block=c.execute('SELECT 1 FROM blocks WHERE space_id=? AND removed_at IS NULL AND start<? AND end>?',(s['id'],d['end'],d['start'])).fetchone()
    check('Bloqueios',not block,'Consulte a agenda e escolha um intervalo sem bloqueio.')
    total=c.execute("SELECT count(*) FROM bookings WHERE user=? AND space_id=? AND substr(start,1,7)=? AND status IN ('pendente','confirmada') AND id!=?",(user,s['id'],d['start'][:7],exclude or -1)).fetchone()[0]
    check('Limite mensal',total<s['monthly_limit'],f"Limite de {s['monthly_limit']} reservas ativas por mês neste espaço.")
    penalty=c.execute('SELECT 1 FROM incidents WHERE user=? AND penalty_until>?',(user,now().isoformat())).fetchone()
    check('Penalidades',not penalty,'Se houver suspensão ativa, procure a gestão antes de reservar.')
    return s,checks

def event(c,bid,user,action,reason):
    c.execute('INSERT INTO audit(booking_id,actor,action,reason,created) VALUES(?,?,?,?,?)',(bid,user,action,reason,now().isoformat(timespec='seconds')))

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/'frontend'/'dist'),**kw)
    def send_json(self,status,data,cookie=None):
        b=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store')
        if cookie: self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def identity(self):
        with connect() as c:
            return auth.session_identity(c, self.session_token())
    def session_token(self):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie','')); token=cookie.get('session')
            return token.value if token else None
        except CookieError: return None
    def session_cookie(self, token='', max_age=0):
        secure='; Secure' if os.environ.get('RESERVA_COOKIE_SECURE')=='1' else ''
        return f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}'
    def do_GET(self):
        if not self.path.startswith('/api/'): return super().do_GET()
        path=urlsplit(self.path).path
        if path not in ('/api/state','/api/reports','/api/reports.csv','/api/users'):
            return self.send_json(404,{'error':'Rota inexistente.'})
        identity=self.identity()
        if not identity: return self.send_json(401,{'error':'Entre com seu usuário e senha.'})
        user=identity['username']; manager=identity['role'] in ('gestor','admin')
        if path=='/api/users':
            if identity['role']!='admin': return self.send_json(403,{'error':'Apenas administradores podem gerenciar contas.'})
            with connect() as c:
                return self.send_json(200,{'users':[dict(x) for x in c.execute('SELECT username,role,active,created FROM users ORDER BY username')]})
        if path in ('/api/reports','/api/reports.csv'):
            if not manager: return self.send_json(403,{'error':'Relatórios disponíveis para gestores e administradores.'})
            try:
                params={key:values[-1] for key,values in parse_qs(urlsplit(self.path).query).items()}
                with connect() as c:
                    c.execute('BEGIN')
                    report=reports.build(c,params,now().date())
                if path.endswith('.csv'):
                    body=reports.to_csv(report); self.send_response(200)
                    self.send_header('Content-Type','text/csv; charset=utf-8')
                    self.send_header('Content-Disposition','attachment; filename="relatorio-reserva-comum.csv"')
                    self.send_header('Cache-Control','no-store')
                    self.send_header('Content-Length',str(len(body)))
                    self.end_headers(); self.wfile.write(body); return
                return self.send_json(200,report)
            except (ValueError,TypeError): return self.send_json(400,{'error':'Período ou espaço inválido. Use datas válidas e um intervalo de 1 a 366 dias.'})
            except sqlite3.Error: return self.send_json(503,{'error':'Banco indisponível. Tente novamente.'})
        with connect() as c:
            spaces=[dict(x) for x in c.execute('SELECT * FROM spaces')]
            bookings=[dict(x) for x in c.execute('SELECT * FROM bookings ORDER BY start')]
            mine=[x for x in bookings if x['user']==user]
            agenda=[{k:x[k] for k in ('id','space_id','start','end','status')} for x in bookings if x['status'] in ('pendente','confirmada')]
            clock=now()
            data={'user':user,'role':identity['role'],'csrf_token':identity['csrf_token'],'spaces':spaces,'agenda':agenda,'blocks':[dict(x) for x in c.execute('SELECT id,space_id,start,end,reason FROM blocks WHERE removed_at IS NULL ORDER BY start')],'mine':mine,'today':clock.date().isoformat(),'server_time':clock.replace(tzinfo=TZ).isoformat(timespec='milliseconds')}
            cutoff=clock.date().isoformat()+'T00:00'
            data['history']=[{k:b[k] for k in ('id','space_id','start','end','status')} for b in bookings if b['start']<cutoff]
            data['history_blocks']=[]
            for block in c.execute('SELECT * FROM blocks WHERE start<? ORDER BY start',(cutoff,)):
                effective_end=min(datetime.fromisoformat(block['end']),datetime.fromisoformat(block['removed_at'])) if block['removed_at'] else datetime.fromisoformat(block['end'])
                if effective_end>datetime.fromisoformat(block['start']):
                    data['history_blocks'].append({**{k:block[k] for k in ('id','space_id','start','reason')},'end':effective_end.isoformat(timespec='seconds')})
            data['decisions']=[dict(x) for x in c.execute("SELECT a.* FROM audit a JOIN bookings b ON b.id=a.booking_id WHERE b.user=? AND a.action IN ('confirmada','rejeitada','cancelada') ORDER BY a.id DESC",(user,))]
            if manager:
                data.update(bookings=bookings,incidents=[dict(x) for x in c.execute('SELECT * FROM incidents')],audit=[dict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC')])
                data['block_audit']=[dict(x) for x in c.execute('SELECT a.*,b.space_id,b.start,b.end FROM block_audit a JOIN blocks b ON b.id=a.block_id ORDER BY a.id DESC')]
            self.send_json(200,data)
    def do_POST(self):
        try:
            origin=self.headers.get('Origin')
            if origin and (urlsplit(origin).netloc!=self.headers.get('Host') or urlsplit(origin).scheme not in ('http','https')):
                return self.send_json(403,{'error':'Origem da solicitação não autorizada.'})
            length=int(self.headers.get('Content-Length','0'))
            if length<0: raise ValueError('Tamanho inválido.')
            if length>16000: return self.send_json(413,{'error':'Solicitação muito grande.'})
            d=json.loads(self.rfile.read(length))
            if not isinstance(d,dict): raise ValueError('Objeto JSON esperado.')
            if self.path=='/api/demo-login': return self.send_json(404,{'error':'Rota inexistente.'})
            if self.path=='/api/login':
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    status,result,token=auth.login(c,d.get('username'),d.get('password'),self.client_address[0],self.session_token())
                return self.send_json(status,result,self.session_cookie(token,auth.SESSION_SECONDS) if token else None)
            identity=self.identity()
            if not identity: return self.send_json(401,{'error':'Sua sessão expirou. Entre novamente.'})
            csrf=self.headers.get('X-CSRF-Token','')
            if not hmac.compare_digest(csrf,identity['csrf_token']):
                return self.send_json(403,{'error':'Solicitação inválida. Atualize a página e tente novamente.'})
            user=identity['username']; role=identity['role']
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                # Recheck inside the write transaction: account/session revocation takes effect immediately.
                identity=auth.session_identity(c,self.session_token())
                if not identity: return self.send_json(401,{'error':'Sua sessão expirou. Entre novamente.'})
                role=identity['role']
                if self.path=='/api/logout':
                    c.execute('DELETE FROM sessions WHERE token_hash=?',(auth.token_hash(self.session_token()),))
                    auth.security_event(c,user,'logout',user); c.commit()
                    return self.send_json(200,{'ok':True},self.session_cookie())
                if self.path=='/api/password':
                    account=c.execute('SELECT * FROM users WHERE username=?',(user,)).fetchone()
                    if not auth.verify_password(d.get('current_password'),account['password_hash']):
                        return self.send_json(400,{'error':'Senha atual incorreta.'})
                    encoded=auth.hash_password(d.get('password'))
                    c.execute('UPDATE users SET password_hash=? WHERE username=?',(encoded,user))
                    c.execute('DELETE FROM sessions WHERE username=?',(user,))
                    auth.security_event(c,user,'senha_alterada',user);c.commit()
                    return self.send_json(200,{'ok':True},self.session_cookie())
                if self.path in ('/api/users','/api/users/update'):
                    if role!='admin': return self.send_json(403,{'error':'Apenas administradores podem gerenciar contas.'})
                    username=auth.normalize_username(d.get('username'))
                    if self.path=='/api/users':
                        if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
                            return self.send_json(409,{'error':'Este nome de usuário já existe.'})
                        auth.create_user(c,username,d.get('password'),d.get('role'),now().isoformat(timespec='seconds'),user)
                    else:
                        account=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
                        if not account: return self.send_json(404,{'error':'Conta inexistente.'})
                        new_role=d.get('role',account['role']); active=d.get('active',bool(account['active']))
                        if new_role not in auth.ROLES or type(active) is not bool: raise ValueError('Perfil ou situação inválidos.')
                        if account['role']=='admin' and account['active'] and (new_role!='admin' or not active):
                            if c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1:
                                return self.send_json(409,{'error':'Mantenha pelo menos um administrador ativo.'})
                        if username==user and (new_role!='admin' or not active or 'password' in d):
                            return self.send_json(409,{'error':'Use outra conta admin para alterar seu perfil/situação; para sua senha, use Minha conta.'})
                        c.execute('UPDATE users SET role=?,active=? WHERE username=?',(new_role,int(active),username))
                        if 'password' in d:
                            c.execute('UPDATE users SET password_hash=? WHERE username=?',(auth.hash_password(d['password']),username))
                        c.execute('DELETE FROM sessions WHERE username=?',(username,))
                        auth.security_event(c,user,'conta_atualizada',username)
                    c.commit();return self.send_json(200,{'ok':True})
                if self.path=='/api/spaces':
                    if role not in ('gestor','admin'):
                        return self.send_json(403,{'error':'Apenas gestores e administradores podem cadastrar espaços.'})
                    for key,minimum,maximum in (('name',3,80),('description',5,500)):
                        if not isinstance(d.get(key),str) or not minimum<=len(d[key].strip())<=maximum:
                            raise ValueError(f'{"Nome" if key=="name" else "Descrição"}: informe de {minimum} a {maximum} caracteres.')
                    for key,minimum,maximum in (('capacity',1,10000),('opens',0,22),('closes',1,23),('notice',0,8760),('monthly_limit',1,1000)):
                        if type(d.get(key)) is not int or not minimum<=d[key]<=maximum:
                            raise ValueError(f'Campo {key}: informe um número inteiro entre {minimum} e {maximum}.')
                    if d['closes']<=d['opens']: raise ValueError('O fechamento deve ser após a abertura, no mesmo dia.')
                    if type(d.get('fee')) not in (int,float) or not 0<=d['fee']<=1000000:
                        raise ValueError('A taxa deve ser um valor entre 0 e 1.000.000.')
                    if type(d.get('approval')) is not bool: raise ValueError('Informe se o espaço exige aprovação.')
                    name=d['name'].strip()
                    if any(row['name'].casefold()==name.casefold() for row in c.execute('SELECT name FROM spaces')):
                        return self.send_json(409,{'error':'Já existe um espaço com esse nome.'})
                    cur=c.execute('''INSERT INTO spaces(name,description,capacity,opens,closes,fee,notice,monthly_limit,approval,rules_version)
                        VALUES(?,?,?,?,?,?,?,?,?,?)''',(name,d['description'].strip(),d['capacity'],d['opens'],d['closes'],d['fee'],d['notice'],d['monthly_limit'],int(d['approval']),'1.0'))
                    auth.security_event(c,user,'espaco_criado',str(cur.lastrowid));c.commit()
                    return self.send_json(200,{'ok':True,'id':cur.lastrowid})
                if self.path in ('/api/blocks','/api/blocks/remove'):
                    if role not in ('gestor','admin'):
                        return self.send_json(403,{'error':'Apenas gestores e administradores podem gerenciar bloqueios.'})
                    if not isinstance(d.get('reason'),str) or not 5<=len(d['reason'].strip())<=500:
                        raise ValueError('Informe um motivo de 5 a 500 caracteres.')
                    reason=d['reason'].strip(); clock=now()
                    if self.path=='/api/blocks/remove':
                        if type(d.get('id')) is not int: raise ValueError('Identificador inválido.')
                        block=c.execute('SELECT * FROM blocks WHERE id=?',(d['id'],)).fetchone()
                        if not block: return self.send_json(404,{'error':'Bloqueio inexistente.'})
                        if block['removed_at']: return self.send_json(200,{'ok':True})
                        if datetime.fromisoformat(block['end'])<=clock:
                            return self.send_json(422,{'error':'Este bloqueio já terminou e faz parte do histórico.'})
                        stamp=clock.isoformat(timespec='seconds')
                        c.execute('UPDATE blocks SET removed_at=? WHERE id=?',(stamp,block['id']))
                        c.execute("INSERT INTO block_audit(block_id,actor,action,reason,created) VALUES(?,?,'remoção',?,?)",(block['id'],user,reason,stamp))
                        c.commit();return self.send_json(200,{'ok':True})
                    if type(d.get('space_id')) is not int: raise ValueError('Espaço inválido.')
                    space=c.execute('SELECT * FROM spaces WHERE id=?',(d['space_id'],)).fetchone()
                    if not space: raise ValueError('Espaço inexistente.')
                    start=datetime.fromisoformat(d['start']);end=datetime.fromisoformat(d['end'])
                    if start.tzinfo or end.tzinfo: raise ValueError('Use o horário local de Fortaleza, sem fuso.')
                    if start.second or start.microsecond or end.second or end.microsecond:
                        raise ValueError('Informe os horários com precisão de minutos.')
                    if end<=start or start.date()!=end.date(): raise ValueError('O fim deve ser após o início, no mesmo dia.')
                    if start<clock: raise ValueError('Escolha um horário de início futuro.')
                    if start.hour<space['opens'] or end>start.replace(hour=space['closes'],minute=0):
                        raise ValueError(f"Escolha horários entre {space['opens']}h e {space['closes']}h.")
                    start=start.isoformat(timespec='minutes');end=end.isoformat(timespec='minutes')
                    if c.execute("SELECT 1 FROM bookings WHERE space_id=? AND status IN ('pendente','confirmada') AND start<? AND end>?",(space['id'],end,start)).fetchone():
                        return self.send_json(409,{'error':'O intervalo possui uma reserva pendente ou confirmada. Escolha outro horário.'})
                    if c.execute('SELECT 1 FROM blocks WHERE space_id=? AND removed_at IS NULL AND start<? AND end>?',(space['id'],end,start)).fetchone():
                        return self.send_json(409,{'error':'O intervalo já possui um bloqueio. Escolha outro horário.'})
                    cur=c.execute('INSERT INTO blocks(space_id,start,end,reason) VALUES(?,?,?,?)',(space['id'],start,end,reason))
                    c.execute("INSERT INTO block_audit(block_id,actor,action,reason,created) VALUES(?,?,'criação',?,?)",(cur.lastrowid,user,reason,clock.isoformat(timespec='seconds')))
                    c.commit();return self.send_json(200,{'ok':True,'id':cur.lastrowid})
                if self.path=='/api/incidents':
                    if role not in ('gestor','admin'): return self.send_json(403,{'error':'Apenas gestores e administradores podem registrar ocorrências.'})
                    if type(d['space_id']) is not int or not c.execute('SELECT 1 FROM spaces WHERE id=?',(d['space_id'],)).fetchone():
                        raise ValueError('Espaço inexistente.')
                    subject=auth.normalize_username(d.get('username'))
                    if not c.execute("SELECT 1 FROM users WHERE username=? AND role='usuario'",(subject,)).fetchone():
                        raise ValueError('Informe uma conta de usuário cadastrada.')
                    for key in ('description','action'):
                        if not isinstance(d.get(key),str) or not 5<=len(d[key].strip())<=500:
                            raise ValueError('Descrição e providência devem ter entre 5 e 500 caracteres.')
                    penalty=None
                    if d.get('penalty_until'):
                        until=datetime.fromisoformat(d['penalty_until'])
                        if until.tzinfo or until<=now(): raise ValueError('A suspensão deve terminar no futuro, no horário de Fortaleza.')
                        penalty=until.isoformat(timespec='minutes')
                    cur=c.execute('INSERT INTO incidents(space_id,user,description,action,penalty_until,created) VALUES(?,?,?,?,?,?)',
                                  (d['space_id'],subject,d['description'].strip(),d['action'].strip(),penalty,now().isoformat(timespec='seconds')))
                    auth.security_event(c,user,'ocorrencia_registrada',subject);c.commit()
                    return self.send_json(200,{'ok':True,'id':cur.lastrowid})
                if self.path in ('/api/validate','/api/bookings'):
                    if role!='usuario': return self.send_json(403,{'error':'Apenas contas de usuário podem solicitar reservas.'})
                    # Normalize dates before lexical comparison in SQLite.
                    for key in ('start','end'): d[key]=datetime.fromisoformat(d[key]).isoformat(timespec='minutes')
                    s,checks=validate(c,d,user); ok=all(x['ok'] for x in checks)
                    result={'ok':ok,'checks':checks}
                    if self.path=='/api/bookings' and ok:
                        status='pendente' if s['approval'] else 'confirmada'
                        cur=c.execute('INSERT INTO bookings(user,space_id,start,end,purpose,guests,status,checks,rules_version) VALUES(?,?,?,?,?,?,?,?,?)',(user,s['id'],d['start'],d['end'],d['purpose'].strip(),d['guests'],status,json.dumps(checks,ensure_ascii=False),s['rules_version']))
                        event(c,cur.lastrowid,user,'criação',status); result.update(id=cur.lastrowid,status=status)
                    c.commit(); return self.send_json(200 if ok or self.path=='/api/validate' else 422,result)
                if self.path=='/api/cancel':
                    if type(d['id']) is not int: raise ValueError('Identificador inválido.')
                    b=c.execute('SELECT * FROM bookings WHERE id=?',(d['id'],)).fetchone()
                    if not b or b['user']!=user: return self.send_json(403,{'error':'Você só pode cancelar suas próprias reservas.'})
                    if b['status']=='cancelada': return self.send_json(200,{'ok':True})
                    if b['status'] not in ('pendente','confirmada'): return self.send_json(409,{'error':'Reserva não pode ser cancelada.'})
                    if datetime.fromisoformat(b['start'])<=now(): return self.send_json(422,{'error':'O prazo terminou: cancele antes do início.'})
                    c.execute("UPDATE bookings SET status='cancelada' WHERE id=?",(b['id'],))
                    event(c,b['id'],user,'cancelada','Cancelamento pelo solicitante, sem multa na demonstração.')
                    c.commit()
                    return self.send_json(200,{'ok':True})
                if self.path=='/api/decision':
                    if role not in ('gestor','admin'): return self.send_json(403,{'error':'Apenas gestores e administradores podem decidir.'})
                    if type(d['id']) is not int: raise ValueError('Identificador inválido.')
                    b=c.execute('SELECT * FROM bookings WHERE id=?',(d['id'],)).fetchone()
                    if not b: return self.send_json(404,{'error':'Pedido inexistente.'})
                    if b['status']!='pendente': return self.send_json(409,{'error':'Este pedido já foi decidido.'})
                    if d.get('status') not in ('confirmada','rejeitada') or not isinstance(d.get('reason'),str) or not 5<=len(d['reason'].strip())<=500: raise ValueError('Informe decisão e justificativa de 5 a 500 caracteres.')
                    if d['status']=='confirmada':
                        payload=dict(b); payload['terms']=True
                        _,checks=validate(c,payload,b['user'],b['id'])
                        if not all(x['ok'] for x in checks): return self.send_json(422,{'error':'As regras mudaram ou o prazo expirou. Rejeite o pedido com justificativa.','checks':checks})
                    c.execute('UPDATE bookings SET status=? WHERE id=?',(d['status'],b['id']))
                    event(c,b['id'],user,d['status'],d['reason'].strip()); c.commit()
                    return self.send_json(200,{'ok':True})
                return self.send_json(404,{'error':'Rota inexistente.'})
        except (ValueError,KeyError,TypeError) as e: self.send_json(400,{'error':'Dados inválidos: '+str(e)})
        except sqlite3.Error: self.send_json(503,{'error':'Banco indisponível. Tente novamente.'})

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Reserva Comum — aplicação local')
    parser.add_argument('--create-admin',action='store_true',help='Criar uma conta admin pelo terminal')
    args=parser.parse_args()
    init()
    if args.create_admin:
        try:
            username=input('Usuário do administrador: ')
            password=getpass.getpass('Senha (10 a 128 caracteres): ')
            if password!=getpass.getpass('Confirme a senha: '): raise ValueError('As senhas não coincidem.')
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                auth.create_user(c,username,password,'admin',now().isoformat(timespec='seconds'),'terminal')
            print('Administrador criado. Inicie o servidor com python3 server.py.')
        except (ValueError,sqlite3.IntegrityError) as error:
            parser.exit(1,f'Não foi possível criar a conta: {error}\n')
    else:
        print('Reserva Comum em http://localhost:8000 (Ctrl+C encerra)')
        ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
