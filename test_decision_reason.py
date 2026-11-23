"""Decision reasons: explicit minimum, maximum, and consistent character counts."""
from test_poc import AppTestCase
import server


class DecisionReasonTests(AppTestCase):
    def pending(self, days=5):
        user=self.client('morador');manager=self.client('gestor')
        result=self.call(user,'bookings',self.payload(days=days))[1]
        self.assertEqual(result['status'],'pendente')
        return manager,result['id']

    def test_minimum_requires_five_characters_without_outer_spaces(self):
        manager,bid=self.pending()
        for reason in ('','a','abcd','  abcd  ','     '):
            with self.subTest(reason=reason):
                status,result=self.call(manager,'decision',{'id':bid,'status':'confirmada','reason':reason})
                self.assertEqual(status,400);self.assertIn('mínimo 5',result['error'])
        with server.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM bookings WHERE id=?',(bid,)).fetchone()[0],'pendente')
            self.assertEqual(c.execute("SELECT count(*) FROM audit WHERE booking_id=? AND action='confirmada'",(bid,)).fetchone()[0],0)
        self.assertEqual(self.call(manager,'decision',{'id':bid,'status':'confirmada','reason':'abcde'})[0],200)

    def test_maximum_is_enforced_for_approval_and_rejection(self):
        for days,decision in ((5,'confirmada'),(6,'rejeitada')):
            manager,bid=self.pending(days)
            for reason in ('x'*201,'x'*199+'  '):
                status,result=self.call(manager,'decision',{'id':bid,'status':decision,'reason':reason})
                self.assertEqual(status,400);self.assertIn('máximo 200',result['error'])
            self.assertEqual(self.call(manager,'decision',{'id':bid,'status':decision,'reason':'x'*200})[0],200)

    def test_unicode_code_points_match_the_frontend_counter(self):
        manager,bid=self.pending()
        reason='😀'*200
        self.assertEqual(self.call(manager,'decision',{'id':bid,'status':'confirmada','reason':reason})[0],200)
        with server.connect() as c:
            stored=c.execute("SELECT reason FROM audit WHERE booking_id=? AND action='confirmada'",(bid,)).fetchone()[0]
            self.assertEqual(len(stored),200);self.assertEqual(stored,reason)
