"""Testes de integração HTTP com SQLite temporário; não alteram a demonstração."""
import unittest, tempfile, threading, json, urllib.request, urllib.error, http.cookiejar
import os, io, secrets
from types import SimpleNamespace
from email.parser import BytesParser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import timedelta
import server
import auth

IN_PROCESS = os.environ.get('RESERVA_TEST_TRANSPORT') == 'memory'


class MemoryConnection:
    """Exercita o parser HTTP e o Handler reais sem abrir uma porta TCP."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()
    def makefile(self, *args): return self.input
    def sendall(self, data): self.output.extend(data)


class AppTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); cls.password=secrets.token_urlsafe(20)
        cls.encoded=auth.hash_password(cls.password)
        if not IN_PROCESS:
            try:
                cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            except Exception:
                cls.temp.cleanup();raise
            cls.url='http://127.0.0.1:'+str(cls.http.server_port)
            threading.Thread(target=cls.http.serve_forever,daemon=True).start()
    def setUp(self):
        server.DB=Path(self.temp.name)/('test-'+secrets.token_hex(6)+'.db');server.init()
        with server.connect() as c:
            for username,role in [('morador','usuario'),('restrito','usuario'),('gestor','gestor'),('admin','admin')]:
                c.execute('INSERT INTO users(username,password_hash,role,created) VALUES(?,?,?,?)',(username,self.encoded,role,server.now().isoformat()))
    @classmethod
    def tearDownClass(cls):
        if not IN_PROCESS: cls.http.shutdown();cls.http.server_close()
        cls.temp.cleanup()
    def client(self,user):
        client=self.anonymous()
        status,_=self.call(client,'login',{'username':user,'password':self.password})
        self.assertEqual(status,200)
        return client
    def anonymous(self):
        client=SimpleNamespace(cookie='') if IN_PROCESS else urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        client.csrf='';return client
    def request(self,client,path,data=None,headers=None):
        headers={'Content-Type':'application/json','X-CSRF-Token':client.csrf,**(headers or {})}
        if IN_PROCESS:
            body=json.dumps(data).encode() if data is not None else b''
            method='POST' if data is not None else 'GET'
            head=f'{method} /api/{path} HTTP/1.0\r\nHost: localhost\r\nCookie: {client.cookie}\r\nContent-Length: {len(body)}\r\n'
            head+=''.join(f'{k}: {v}\r\n' for k,v in headers.items())
            connection=MemoryConnection(head.encode()+b'\r\n'+body)
            server.Handler(connection,('127.0.0.1',12345),SimpleNamespace())
            head,body=bytes(connection.output).split(b'\r\n\r\n',1)
            line,rest=head.split(b'\r\n',1);response_headers=BytesParser().parsebytes(rest+b'\r\n\r\n')
            if response_headers.get('Set-Cookie'):client.cookie=response_headers['Set-Cookie'].split(';',1)[0]
            return int(line.split()[1]),body,response_headers
        req=urllib.request.Request(self.url+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with client.open(req) as r:return r.status,r.read(),r.headers
        except urllib.error.HTTPError as e:return e.code,e.read(),e.headers
    def call(self,client,path,data=None,headers=None):
        status,body,_=self.request(client,path,data,headers)
        result=json.loads(body)
        if result.get('csrf_token'):client.csrf=result['csrf_token']
        return status,result
    def payload(self,days=5,space=1):
        day=(server.now()+timedelta(days=days)).date().isoformat()
        return {'space_id':space,'start':day+'T14:00','end':day+'T16:00','purpose':'Almoço em família','guests':5,'terms':True}


class PocTests(AppTestCase):
    def test_01_consulta_e_privacidade(self):
        status,data=self.call(self.client('morador'),'state');self.assertEqual(status,200)
        self.assertEqual(len(data['spaces']),2)
        self.assertNotIn('user',data['agenda'][0]);self.assertNotIn('incidents',data)
    def test_02_validacoes(self):
        cl=self.client('morador')
        for field,value,rule in [('guests',99,'Capacidade'),('terms',False,'Termos'),('end',self.payload()['start'],'Intervalo')]:
            d=self.payload();d[field]=value
            status,r=self.call(cl,'bookings',d);self.assertEqual(status,422)
            self.assertFalse(next(x for x in r['checks'] if x['name']==rule)['ok'])
        d=self.payload(days=3,space=2);d.update(start=d['start'][:10]+'T10:00',end=d['end'][:10]+'T11:00')
        self.assertFalse(self.call(cl,'validate',d)[1]['ok'])
        self.assertFalse(self.call(self.client('restrito'),'validate',self.payload())[1]['ok'])
    def test_03_fluxo_gestor_e_justificativa(self):
        cl=self.client('morador');gestor=self.client('gestor')
        status,r=self.call(cl,'bookings',self.payload(days=6));self.assertEqual(r['status'],'pendente');bid=r['id']
        decision={'id':bid,'status':'confirmada','reason':'Regras verificadas e ocorrência orientada.'}
        self.assertEqual(self.call(cl,'decision',decision)[0],403)
        self.assertEqual(self.call(gestor,'decision',{**decision,'reason':''})[0],400)
        self.assertEqual(self.call(gestor,'decision',decision)[0],200)
        self.assertEqual(self.call(gestor,'decision',decision)[0],409)
        d=self.call(cl,'state')[1];self.assertEqual(d['decisions'][0]['reason'],decision['reason'])
        with server.connect() as c:
            with self.assertRaises(server.sqlite3.IntegrityError): c.execute('DELETE FROM audit')
    def test_04_concorrencia(self):
        a=self.client('morador');b=self.client('morador');d=self.payload(days=7)
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda cl:self.call(cl,'bookings',d),[a,b]))
        self.assertEqual(sorted(x[0] for x in results),[200,422])
    def test_05_confirmacao_automatica_e_rejeicao(self):
        cl=self.client('morador');g=self.client('gestor')
        r=self.call(cl,'bookings',self.payload(days=8,space=2))[1];self.assertEqual(r['status'],'confirmada')
        r=self.call(cl,'bookings',self.payload(days=9))[1]
        self.assertEqual(self.call(g,'decision',{'id':r['id'],'status':'rejeitada','reason':'Manutenção extraordinária prevista.'})[0],200)
        self.assertEqual(self.call(cl,'bookings',self.payload(days=9))[0],200)
    def test_06_revalidacao_e_limites(self):
        cl=self.client('morador');g=self.client('gestor')
        # Banco dedicado evita dependência do mês corrente e de outros testes.
        with server.connect() as c: c.execute('UPDATE spaces SET monthly_limit=100 WHERE id=1')
        d=self.payload(days=10);r=self.call(cl,'bookings',d)[1]
        with server.connect() as c:
            c.execute('INSERT INTO blocks(space_id,start,end,reason) VALUES(1,?,?,?)',(d['start'],d['end'],'Manutenção'))
        self.assertEqual(self.call(g,'decision',{'id':r['id'],'status':'confirmada','reason':'Tentativa de aprovação.'})[0],422)
        with server.connect() as c: c.execute('UPDATE spaces SET monthly_limit=0 WHERE id=1')
        r=self.call(cl,'validate',self.payload(days=11))[1]
        self.assertFalse(next(x for x in r['checks'] if x['name']=='Limite mensal')['ok'])

    def test_07_cancelamento(self):
        cl=self.client('morador'); other=self.client('restrito')
        d=self.payload(days=15,space=2)
        r=self.call(cl,'bookings',d)[1]; bid=r['id']
        self.assertEqual(self.call(other,'cancel',{'id':bid})[0],403)
        self.assertEqual(self.call(cl,'cancel',{'id':bid})[0],200)
        self.assertEqual(self.call(cl,'cancel',{'id':bid})[0],200)
        state=self.call(cl,'state')[1]
        self.assertNotIn(bid,[x['id'] for x in state['agenda']])
        self.assertEqual(len([x for x in state['decisions'] if x['booking_id']==bid and x['action']=='cancelada']),1)
        self.assertEqual(self.call(cl,'bookings',d)[0],200)

    def test_08_cancelamento_fora_prazo(self):
        cl=self.client('morador')
        d=self.payload(days=16,space=2)
        bid=self.call(cl,'bookings',d)[1]['id']
        with server.connect() as c:
            c.execute('UPDATE bookings SET start=? WHERE id=?',((server.now()-timedelta(hours=1)).isoformat(),bid))
        self.assertEqual(self.call(cl,'cancel',{'id':bid})[0],422)

if __name__=='__main__': unittest.main(verbosity=2)
