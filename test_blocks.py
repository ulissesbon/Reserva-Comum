"""Bloqueios gerenciais: permissão, conflitos, concorrência e rastreabilidade."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

import reports
import server
from test_poc import AppTestCase


class BlockTests(AppTestCase):
    def block(self, **changes):
        payload={key:value for key,value in self.payload().items() if key in ('space_id','start','end')}
        return {**payload,'reason':'Manutenção programada.',**changes}

    def test_permissions_and_csrf(self):
        user=self.client('morador');manager=self.client('gestor')
        self.assertEqual(self.call(self.anonymous(),'blocks',self.block())[0],401)
        self.assertEqual(self.call(user,'blocks',self.block())[0],403)
        self.assertEqual(self.call(manager,'blocks',self.block(),{'X-CSRF-Token':''})[0],403)
        bid=self.call(manager,'blocks',self.block())[1]['id']
        removal={'id':bid,'reason':'Manutenção finalizada.'}
        self.assertEqual(self.call(user,'blocks/remove',removal)[0],403)
        self.assertEqual(self.call(manager,'blocks/remove',removal,{'X-CSRF-Token':''})[0],403)
        self.assertEqual(self.call(self.client('admin'),'blocks/remove',removal)[0],200)

    def test_block_prevents_booking_and_is_visible_without_internal_history(self):
        manager=self.client('gestor');user=self.client('morador')
        status,result=self.call(manager,'blocks',self.block())
        self.assertEqual(status,200)
        status,result=self.call(user,'bookings',self.payload())
        self.assertEqual(status,422)
        self.assertFalse(next(c for c in result['checks'] if c['name']=='Bloqueios')['ok'])
        state=self.call(user,'state')[1]
        self.assertTrue(any(b['reason']=='Manutenção programada.' for b in state['blocks']))
        self.assertNotIn('block_audit',state)
        self.assertTrue(all('removed_at' not in b for b in state['blocks']))
        self.assertEqual(self.call(manager,'state')[1]['block_audit'][0]['actor'],'gestor')

    def test_invalid_input_does_not_create_blocks(self):
        manager=self.client('gestor');d=self.block();day=d['start'][:10]
        cases=[{'space_id':True},{'space_id':999},{'reason':' '},{'reason':'x'*501},
               {'start':day+'T16:00','end':day+'T14:00'},
               {'start':day+'T07:00'}, {'end':day+'T23:00'},
               {'start':day+'T14:00:01'}, {'start':day+'T14:00-03:00'},
               {'end':(datetime.fromisoformat(day)+timedelta(days=1)).isoformat()},
               {'start':'data inválida'}, {'start':(server.now()-timedelta(days=1)).isoformat(timespec='minutes')}]
        with server.connect() as c:before=c.execute('SELECT count(*) FROM blocks').fetchone()[0]
        for change in cases:
            with self.subTest(change=change):self.assertEqual(self.call(manager,'blocks',{**d,**change})[0],400)
        with server.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM blocks').fetchone()[0],before)
            self.assertEqual(c.execute('SELECT count(*) FROM block_audit').fetchone()[0],0)

    def test_pending_and_confirmed_bookings_cannot_be_blocked(self):
        manager=self.client('gestor');user=self.client('morador')
        for space in (1,2):
            d=self.payload(space=space)
            self.assertEqual(self.call(user,'bookings',d)[0],200)
            self.assertEqual(self.call(manager,'blocks',{**self.block(), 'space_id':space})[0],409)

    def test_overlapping_blocks_rejected_but_adjacent_allowed(self):
        manager=self.client('gestor');d=self.block();day=d['start'][:10]
        self.assertEqual(self.call(manager,'blocks',d)[0],200)
        self.assertEqual(self.call(manager,'blocks',d)[0],409)
        self.assertEqual(self.call(manager,'blocks',{**d,'start':day+'T15:00','end':day+'T17:00'})[0],409)
        self.assertEqual(self.call(manager,'blocks',{**d,'start':day+'T16:00','end':day+'T17:00'})[0],200)
        self.assertEqual(self.call(manager,'blocks',{**d,'space_id':2})[0],200)

    def test_removal_releases_booking_and_preserves_immutable_audit(self):
        manager=self.client('gestor');user=self.client('morador')
        bid=self.call(manager,'blocks',self.block())[1]['id']
        d={'id':bid,'reason':'Serviço não será mais necessário.'}
        self.assertEqual(self.call(manager,'blocks/remove',{**d,'reason':'x'})[0],400)
        self.assertEqual(self.call(manager,'blocks/remove',d)[0],200)
        self.assertEqual(self.call(manager,'blocks/remove',d)[0],200)
        self.assertNotIn(bid,[b['id'] for b in self.call(user,'state')[1]['blocks']])
        self.assertEqual(self.call(user,'bookings',self.payload())[0],200)
        with server.connect() as c:
            events=c.execute('SELECT * FROM block_audit WHERE block_id=? ORDER BY id',(bid,)).fetchall()
            self.assertEqual([e['action'] for e in events],['criação','remoção'])
            self.assertIsNotNone(c.execute('SELECT removed_at FROM blocks WHERE id=?',(bid,)).fetchone()[0])
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM block_audit')
            with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE block_audit SET reason='Alterado'")

    def test_removal_updates_reports_without_erasing_elapsed_blocked_hours(self):
        manager=self.client('gestor');d=self.block();day=d['start'][:10]
        bid=self.call(manager,'blocks',d)[1]['id']
        def available():
            with server.connect() as c:
                return reports.build(c,{'from':day,'to':day,'space_id':'1'},server.now().date())['spaces'][0]['available_hours']
        self.assertEqual(available(),12)
        with patch.object(server,'now',return_value=datetime.fromisoformat(day+'T15:00')):
            self.assertEqual(self.call(manager,'blocks/remove',{'id':bid,'reason':'Serviço finalizado antecipadamente.'})[0],200)
        self.assertEqual(available(),13)

    def test_expired_block_cannot_be_removed(self):
        manager=self.client('gestor');d=self.block()
        bid=self.call(manager,'blocks',d)[1]['id']
        with patch.object(server,'now',return_value=datetime.fromisoformat(d['end'])+timedelta(minutes=1)):
            self.assertEqual(self.call(manager,'blocks/remove',{'id':bid,'reason':'Tentativa posterior ao término.'})[0],422)
        self.assertEqual(self.call(manager,'blocks/remove',{'id':99999,'reason':'Identificador inexistente.'})[0],404)

    def test_concurrent_booking_and_block_have_only_one_winner(self):
        manager=self.client('gestor');user=self.client('morador')
        with ThreadPoolExecutor(2) as pool:
            jobs=[pool.submit(self.call,manager,'blocks',self.block()),pool.submit(self.call,user,'bookings',self.payload())]
            results=[job.result()[0] for job in jobs]
        self.assertEqual(results.count(200),1)
        self.assertIn(results[0],(200,409));self.assertIn(results[1],(200,422))
        with server.connect() as c:
            blocks=c.execute('SELECT count(*) FROM blocks WHERE start=? AND space_id=1 AND removed_at IS NULL',(self.payload()['start'],)).fetchone()[0]
            bookings=c.execute("SELECT count(*) FROM bookings WHERE start=? AND space_id=1 AND status IN ('pendente','confirmada')",(self.payload()['start'],)).fetchone()[0]
            self.assertEqual(blocks+bookings,1)

    def test_legacy_blocks_migrate_without_data_loss(self):
        original=server.DB
        try:
            server.DB=original.parent/'old-blocks.db'
            with sqlite3.connect(server.DB) as c:
                c.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY,space_id INTEGER,start TEXT,end TEXT,reason TEXT)')
                c.execute("INSERT INTO blocks VALUES(500,1,'2026-11-18T10:00','2026-11-18T12:00','Limpeza anterior')")
            server.init();server.init()
            with server.connect() as c:
                row=c.execute('SELECT * FROM blocks WHERE id=500').fetchone()
                self.assertEqual(row['reason'],'Limpeza anterior');self.assertIsNone(row['removed_at'])
        finally:server.DB=original
