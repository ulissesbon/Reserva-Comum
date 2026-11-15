"""Regressões de autenticação, autorização, migração e relatórios RF15."""
import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import auth
import reports
import server
from test_poc import AppTestCase


class AuthenticationTests(AppTestCase):
    def test_login_cookie_and_hashed_storage(self):
        client=self.anonymous()
        status,raw,headers=self.request(client,'login',{'username':' MORADOR ','password':self.password,'role':'admin'})
        self.assertEqual(status,200)
        data=json.loads(raw);self.assertEqual(data['role'],'usuario')
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertIn('Max-Age=28800',headers['Set-Cookie'])
        with server.connect() as c:
            row=c.execute("SELECT * FROM users WHERE username='morador'").fetchone()
            self.assertNotEqual(row['password_hash'],self.password)
            self.assertTrue(auth.verify_password(self.password,row['password_hash']))
            tokens=[s['token_hash'] for s in c.execute('SELECT * FROM sessions')]
        if hasattr(client,'cookie'):self.assertNotIn(client.cookie.removeprefix('session='),tokens)

    def test_wrong_password_unknown_user_and_rate_limit(self):
        client=self.anonymous()
        for username in ('morador','inexistente'):
            status,result=self.call(client,'login',{'username':username,'password':'senha-incorreta'})
            self.assertEqual(status,401);self.assertEqual(result['error'],'Usuário ou senha inválidos.')
        for _ in range(4):self.assertEqual(self.call(client,'login',{'username':'morador','password':'incorreta'})[0],401)
        self.assertEqual(self.call(client,'login',{'username':'morador','password':self.password})[0],429)

    def test_no_anonymous_or_demo_access(self):
        client=self.anonymous()
        for route in ('state','users','reports','reports.csv'):
            self.assertEqual(self.call(client,route)[0],401)
        self.assertEqual(self.call(client,'demo-login',{'user':'gestor'})[0],404)
        self.assertEqual(self.call(client,'nao-existe')[0],404)

    def test_csrf_and_cross_origin(self):
        client=self.client('morador')
        self.assertEqual(self.call(client,'bookings',self.payload(),{'X-CSRF-Token':''})[0],403)
        self.assertEqual(self.call(client,'bookings',self.payload(),{'Origin':'https://outro.example'})[0],403)
        self.assertEqual(self.call(client,'bookings',self.payload())[0],200)

    def test_logout_and_expiration(self):
        client=self.client('morador')
        self.assertEqual(self.call(client,'logout',{})[0],200)
        self.assertEqual(self.call(client,'state')[0],401)
        client=self.client('morador')
        with server.connect() as c:c.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.call(client,'state')[0],401)

    def test_permissions_depend_on_role_not_username(self):
        with server.connect() as c:
            c.execute("UPDATE users SET role='usuario' WHERE username='gestor'")
            c.execute("UPDATE users SET role='gestor' WHERE username='morador'")
        named_manager=self.client('gestor');real_manager=self.client('morador')
        self.assertEqual(self.call(named_manager,'reports')[0],403)
        self.assertEqual(self.call(real_manager,'reports')[0],200)
        self.assertEqual(self.call(real_manager,'bookings',self.payload())[0],403)
        state=self.call(named_manager,'state')[1]
        for key in ('bookings','audit','incidents'):self.assertNotIn(key,state)

    def test_create_account_only_admin_and_no_password_exposure(self):
        user=self.client('morador');manager=self.client('gestor');admin=self.client('admin')
        payload={'username':'ana','password':'senha-segura-123','role':'usuario'}
        for client in (user,manager):
            self.assertEqual(self.call(client,'users',payload)[0],403)
            self.assertEqual(self.call(client,'users')[0],403)
        self.assertEqual(self.call(admin,'users',payload)[0],200)
        self.assertEqual(self.call(admin,'users',payload)[0],409)
        self.assertEqual(self.call(admin,'users',{**payload,'username':'bia','role':'superadmin'})[0],400)
        data=self.call(admin,'users')[1]
        self.assertTrue(all('password_hash' not in u and 'password' not in u for u in data['users']))
        self.assertEqual(self.call(self.anonymous(),'login',{'username':'ana','password':payload['password']})[0],200)

    def test_deactivation_role_change_and_password_reset_revoke_sessions(self):
        admin=self.client('admin');user=self.client('morador')
        self.assertEqual(self.call(admin,'users/update',{'username':'morador','role':'gestor','active':True})[0],200)
        self.assertEqual(self.call(user,'state')[0],401)
        user=self.client('morador');self.assertEqual(self.call(user,'reports')[0],200)
        self.assertEqual(self.call(admin,'users/update',{'username':'morador','password':'outra-senha-123'})[0],200)
        self.assertEqual(self.call(user,'state')[0],401)
        self.assertEqual(self.call(self.anonymous(),'login',{'username':'morador','password':self.password})[0],401)
        self.assertEqual(self.call(admin,'users/update',{'username':'morador','active':False})[0],200)
        self.assertEqual(self.call(self.anonymous(),'login',{'username':'morador','password':'outra-senha-123'})[0],401)

    def test_last_admin_cannot_be_removed(self):
        admin=self.client('admin')
        for update in ({'role':'usuario'},{'active':False}):
            self.assertEqual(self.call(admin,'users/update',{'username':'admin',**update})[0],409)

    def test_change_password_checks_current_and_revokes_all_sessions(self):
        first=self.client('morador');second=self.client('morador')
        self.assertEqual(self.call(first,'password',{'current_password':'incorreta','password':'nova-senha-123'})[0],400)
        self.assertEqual(self.call(first,'password',{'current_password':self.password,'password':'curta'})[0],400)
        self.assertEqual(self.call(first,'password',{'current_password':self.password,'password':'nova-senha-123'})[0],200)
        self.assertEqual(self.call(second,'state')[0],401)
        self.assertEqual(self.call(self.anonymous(),'login',{'username':'morador','password':'nova-senha-123'})[0],200)

    def test_user_cannot_cancel_another_account(self):
        client=self.client('morador');admin=self.client('admin')
        bid=self.call(client,'bookings',self.payload())[1]['id']
        self.assertEqual(self.call(admin,'cancel',{'id':bid})[0],403)

    def test_secure_cookie_option(self):
        with patch.dict('os.environ',{'RESERVA_COOKIE_SECURE':'1'}):
            _,_,headers=self.request(self.anonymous(),'login',{'username':'morador','password':self.password})
        self.assertIn('; Secure',headers['Set-Cookie'])

    def test_incident_registration_permissions_and_suspension(self):
        user=self.client('morador');manager=self.client('gestor')
        penalty=(server.now()+timedelta(days=30)).isoformat(timespec='minutes')
        payload={'space_id':1,'username':'morador','description':'Descumpriu o horário de saída.',
                 'action':'Suspensão registrada pela gestão.','penalty_until':penalty}
        self.assertEqual(self.call(user,'incidents',payload)[0],403)
        self.assertEqual(self.call(manager,'incidents',{**payload,'username':'inexistente'})[0],400)
        self.assertEqual(self.call(manager,'incidents',payload)[0],200)
        self.assertEqual(self.call(user,'bookings',self.payload())[0],422)
        with server.connect() as c:
            row=c.execute('SELECT * FROM incidents ORDER BY id DESC LIMIT 1').fetchone()
            self.assertIsNotNone(row['created']);self.assertEqual(row['penalty_until'],penalty)
        state=self.call(user,'state')[1];self.assertNotIn('incidents',state)

    def test_failed_user_update_rolls_back_role_and_preserves_session(self):
        admin=self.client('admin');user=self.client('morador')
        self.assertEqual(self.call(admin,'users/update',{'username':'morador','role':'gestor','password':'curta'})[0],400)
        self.assertEqual(self.call(user,'state')[1]['role'],'usuario')


class ReportTests(AppTestCase):
    def fixture(self):
        day=(server.now()+timedelta(days=5)).date().isoformat()
        with server.connect() as c:
            c.execute('DELETE FROM bookings');c.execute('DELETE FROM blocks');c.execute('DELETE FROM incidents')
            for status,start,end in [('confirmada','10:00','12:00'),('cancelada','14:00','15:00'),('pendente','16:00','17:00'),('rejeitada','18:00','19:00')]:
                cur=c.execute('''INSERT INTO bookings(user,space_id,start,end,purpose,guests,status,checks,rules_version)
                    VALUES('morador',1,?,?,'Reunião',5,?,'[]','1.0')''',(day+'T'+start,day+'T'+end,status))
                if status=='confirmada':
                    c.execute("INSERT INTO audit(booking_id,actor,action,reason,created) VALUES(?,'morador','criação','pendente',?)",(cur.lastrowid,day+'T08:00:00'))
                    c.execute("INSERT INTO audit(booking_id,actor,action,reason,created) VALUES(?,'gestor','confirmada','Aprovada',?)",(cur.lastrowid,day+'T08:30:00'))
            for start,end in [('08:00','09:00'),('08:30','09:30')]:
                c.execute('INSERT INTO blocks(space_id,start,end,reason) VALUES(1,?,?,?)',(day+'T'+start,day+'T'+end,'Limpeza'))
            for created in (day+'T10:00:00',None,(datetime.fromisoformat(day)-timedelta(days=1)).isoformat()):
                c.execute("INSERT INTO incidents(space_id,user,description,action,created) VALUES(1,'morador','Ocorrência','Orientação',?)",(created,))
        return day

    def test_counts_occupancy_approval_and_dated_incidents(self):
        day=self.fixture();manager=self.client('gestor')
        status,r=self.call(manager,f'reports?from={day}&to={day}&space_id=1')
        self.assertEqual(status,200);row=r['spaces'][0]
        self.assertEqual(row['reservations'],4)
        for status in ('confirmada','pendente','cancelada','rejeitada'):self.assertEqual(row[status],1)
        self.assertEqual(row['confirmed_hours'],2)
        self.assertEqual(row['available_hours'],12.5) # 14h abertas - união de bloqueios de 1,5h
        self.assertEqual(row['occupancy_pct'],16)
        self.assertEqual(row['avg_approval_minutes'],30)
        self.assertEqual(row['approval_samples'],1)
        self.assertEqual(row['incidents'],1);self.assertEqual(row['undated_incidents'],1)

    def test_empty_period_and_no_approval_samples(self):
        day=self.fixture()
        with server.connect() as c:
            result=reports.build(c,{'from':day,'to':day,'space_id':'2'},server.now().date())
        self.assertEqual(result['totals']['reservations'],0)
        self.assertEqual(result['spaces'][0]['occupancy_pct'],0)
        self.assertIsNone(result['spaces'][0]['avg_approval_minutes'])

    def test_csv_export_and_permissions(self):
        day=self.fixture();manager=self.client('gestor')
        status,body,headers=self.request(manager,f'reports.csv?from={day}&to={day}')
        self.assertEqual(status,200);self.assertIn('text/csv',headers['Content-Type'])
        self.assertIn('attachment',headers['Content-Disposition'])
        rows=list(csv.reader(io.StringIO(body.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),3);self.assertEqual(len(rows[0]),len(rows[1]))
        self.assertEqual(self.call(self.client('morador'),'reports')[0],403)
        self.assertEqual(self.call(self.client('morador'),'reports.csv')[0],403)
        self.assertEqual(self.call(self.client('admin'),'reports')[0],200)

    def test_invalid_filters_and_date_overflow(self):
        manager=self.client('gestor')
        for query in ('from=2026-02-30','from=2026-11-20&to=2026-11-01',
                      'from=2020-01-01&to=2026-01-01','space_id=99','space_id=abc',
                      'from=9999-12-31&to=9999-12-31'):
            self.assertEqual(self.call(manager,'reports?'+query)[0],400)

    def test_blocked_time_cannot_produce_occupancy_over_100(self):
        day=self.fixture()
        with server.connect() as c:
            c.execute('INSERT INTO blocks(space_id,start,end,reason) VALUES(1,?,?,?)',(day+'T08:00',day+'T22:00','Fechado'))
            result=reports.build(c,{'from':day,'to':day,'space_id':'1'},server.now().date())
        self.assertEqual(result['spaces'][0]['available_hours'],0)
        self.assertEqual(result['spaces'][0]['confirmed_hours'],0)
        self.assertEqual(result['spaces'][0]['occupancy_pct'],0)

    def test_legacy_database_migration_preserves_data(self):
        original=server.DB
        try:
            server.DB=original.parent/'legacy.db'
            with sqlite3.connect(server.DB) as c:
                c.execute('CREATE TABLE incidents(id INTEGER PRIMARY KEY,space_id INTEGER,user TEXT,description TEXT,action TEXT,penalty_until TEXT)')
                c.execute("INSERT INTO incidents VALUES(500,1,'antigo','Registro antigo','Orientação',NULL)")
            server.init();server.init()
            with server.connect() as c:
                incident=c.execute('SELECT * FROM incidents WHERE id=500').fetchone()
                self.assertEqual(incident['description'],'Registro antigo');self.assertIsNone(incident['created'])
                self.assertEqual(c.execute('SELECT count(*) FROM spaces').fetchone()[0],2)
        finally:server.DB=original
