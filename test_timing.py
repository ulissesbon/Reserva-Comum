"""Clock synchronization and unchanged advance-notice boundaries."""
from datetime import datetime, timedelta
from unittest.mock import patch

import server
from test_poc import AppTestCase


class TimingTests(AppTestCase):
    def request_data(self, space, start, end):
        return {'space_id':space,'start':start,'end':end,'purpose':'Encontro em família',
                'guests':2,'terms':True}

    def test_state_clock_includes_fortaleza_offset_and_consistent_today(self):
        user=self.client('morador')
        clock=datetime(2026,11,15,23,59,59,123000)
        with patch.object(server,'now',return_value=clock):
            status,data=self.call(user,'state')
        self.assertEqual(status,200)
        self.assertEqual(data['today'],'2026-11-15')
        self.assertEqual(data['server_time'],'2026-11-15T23:59:59.123-03:00')
        with patch.object(server,'now',return_value=clock+timedelta(seconds=1)):
            self.assertEqual(self.call(user,'state')[1]['today'],'2026-11-16')

    def test_pool_requires_full_two_hours_and_keeps_automatic_confirmation(self):
        user=self.client('morador')
        with patch.object(server,'now',return_value=datetime(2026,11,15,17)):
            early=self.request_data(2,'2026-11-15T18:59','2026-11-15T19:30')
            self.assertEqual(self.call(user,'bookings',early)[0],422)
            valid=self.request_data(2,'2026-11-15T19:00','2026-11-15T20:00')
            status,result=self.call(user,'bookings',valid)
            self.assertEqual(status,200);self.assertEqual(result['status'],'confirmada')

    def test_barbecue_requires_full_twenty_four_hours_and_keeps_manual_approval(self):
        user=self.client('morador')
        with patch.object(server,'now',return_value=datetime(2026,11,15,17)):
            early=self.request_data(1,'2026-11-16T16:59','2026-11-16T18:00')
            self.assertEqual(self.call(user,'bookings',early)[0],422)
            valid=self.request_data(1,'2026-11-16T17:00','2026-11-16T18:00')
            status,result=self.call(user,'bookings',valid)
            self.assertEqual(status,200);self.assertEqual(result['status'],'pendente')

    def test_past_and_recently_started_reservations_are_rejected_without_grace(self):
        user=self.client('morador')
        with patch.object(server,'now',return_value=datetime(2026,11,15,17,5)):
            for space in (1,2):
                for day in ('2026-11-14','2026-11-15'):
                    with self.subTest(space=space,day=day):
                        data=self.request_data(space,day+'T17:00',day+'T18:00')
                        status,result=self.call(user,'bookings',data)
                        self.assertEqual(status,422)
                        self.assertFalse(next(x for x in result['checks'] if x['name']=='Antecedência')['ok'])

    def test_blocks_reject_past_start_and_accept_future_minute(self):
        manager=self.client('gestor')
        with patch.object(server,'now',return_value=datetime(2026,11,15,17,5,30)):
            payload={'space_id':1,'start':'2026-11-15T17:05','end':'2026-11-15T18:00','reason':'Manutenção programada.'}
            self.assertEqual(self.call(manager,'blocks',payload)[0],400)
            self.assertEqual(self.call(manager,'blocks',{**payload,'start':'2026-11-15T17:06'})[0],200)

    def test_approval_does_not_restart_the_original_advance_notice_window(self):
        user=self.client('morador');manager=self.client('gestor')
        payload=self.request_data(1,'2026-11-16T18:00','2026-11-16T19:00')
        with patch.object(server,'now',return_value=datetime(2026,11,15,17,49)):
            status,result=self.call(user,'bookings',payload)
            self.assertEqual(status,200);bid=result['id']
        with patch.object(server,'now',return_value=datetime(2026,11,15,18,8)):
            self.assertEqual(self.call(manager,'decision',{'id':bid,'status':'confirmada','reason':'Regras verificadas.'})[0],200)

    def test_approval_is_refused_at_start_with_an_explicit_failed_rule(self):
        user=self.client('morador');manager=self.client('gestor')
        payload=self.request_data(1,'2026-11-16T18:00','2026-11-16T19:00')
        with patch.object(server,'now',return_value=datetime(2026,11,15,17,49)):
            bid=self.call(user,'bookings',payload)[1]['id']
        with patch.object(server,'now',return_value=datetime(2026,11,16,18)):
            status,result=self.call(manager,'decision',{'id':bid,'status':'confirmada','reason':'Tentativa após o início.'})
        self.assertEqual(status,422)
        self.assertEqual([c['name'] for c in result['checks'] if not c['ok']],['Prazo de aprovação'])
        with server.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM bookings WHERE id=?',(bid,)).fetchone()[0],'pendente')
