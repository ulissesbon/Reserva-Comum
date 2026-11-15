"""Privacy-preserving historical agenda reads."""
from datetime import timedelta
from test_poc import AppTestCase
import server


class HistoricalAgendaTests(AppTestCase):
    def test_all_past_statuses_are_visible_without_personal_data(self):
        day=(server.now()-timedelta(days=2)).date().isoformat()
        with server.connect() as c:
            for status in ('confirmada','pendente','cancelada','rejeitada'):
                c.execute('''INSERT INTO bookings(user,space_id,start,end,purpose,guests,status,checks,rules_version)
                    VALUES('terceiro',1,?,?,'Finalidade privada',5,?,'[]','1.0')''',(day+'T10:00',day+'T11:00',status))
        result=self.call(self.client('morador'),'state')[1]
        history=[b for b in result['history'] if b['start'].startswith(day)]
        self.assertEqual({b['status'] for b in history},{'confirmada','pendente','cancelada','rejeitada'})
        for b in history:self.assertEqual(set(b),{'id','space_id','start','end','status'})

    def test_removed_blocks_retain_only_the_interval_that_actually_elapsed(self):
        day=(server.now()-timedelta(days=2)).date().isoformat()
        with server.connect() as c:
            first=c.execute('INSERT INTO blocks(space_id,start,end,reason,removed_at) VALUES(1,?,?,?,?)',
                            (day+'T10:00',day+'T12:00','Limpeza',day+'T10:30')).lastrowid
            second=c.execute('INSERT INTO blocks(space_id,start,end,reason,removed_at) VALUES(1,?,?,?,?)',
                             (day+'T14:00',day+'T16:00','Cancelado antes do início',day+'T13:00')).lastrowid
        result=self.call(self.client('morador'),'state')[1]
        history=result['history_blocks']
        self.assertEqual(next(b['end'] for b in history if b['id']==first),day+'T10:30:00')
        self.assertNotIn(second,[b['id'] for b in history])
        self.assertNotIn(first,[b['id'] for b in result['blocks']])
