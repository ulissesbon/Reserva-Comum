"""Configurable-space creation and rule validation."""
from test_poc import AppTestCase


class SpaceTests(AppTestCase):
    def space_data(self, **changes):
        return {**{'name':'Salão de festas','description':'Espaço para encontros dos moradores.',
                   'capacity':5,'opens':9,'closes':18,'fee':25.5,'notice':3,
                   'monthly_limit':2,'approval':True},**changes}

    def test_creation_permissions_and_persistence(self):
        payload=self.space_data()
        self.assertEqual(self.call(self.anonymous(),'spaces',payload)[0],401)
        self.assertEqual(self.call(self.client('morador'),'spaces',payload)[0],403)
        manager=self.client('gestor')
        self.assertEqual(self.call(manager,'spaces',payload,{'X-CSRF-Token':''})[0],403)
        status,result=self.call(manager,'spaces',payload);self.assertEqual(status,200)
        spaces=self.call(self.client('morador'),'state')[1]['spaces']
        created=next(s for s in spaces if s['id']==result['id'])
        for key in ('name','description','capacity','opens','closes','fee','notice','monthly_limit'):
            self.assertEqual(created[key],payload[key])
        self.assertEqual(created['approval'],1)
        self.assertEqual(self.call(self.client('admin'),'spaces',self.space_data(name='Sala de jogos'))[0],200)

    def test_invalid_parameters_and_duplicate_names_are_rejected(self):
        manager=self.client('gestor')
        for change in ({'capacity':0},{'capacity':True},{'opens':-1},{'closes':24},
                       {'opens':18,'closes':18},{'fee':-1},{'fee':float('nan')},
                       {'notice':-1},{'monthly_limit':0},{'approval':'yes'},{'name':'x'}):
            with self.subTest(change=change):
                self.assertEqual(self.call(manager,'spaces',self.space_data(**change))[0],400)
        self.assertEqual(self.call(manager,'spaces',self.space_data())[0],200)
        self.assertEqual(self.call(manager,'spaces',self.space_data(name='  SALÃO DE FESTAS  '))[0],409)

    def test_new_space_rules_drive_existing_booking_validation(self):
        manager=self.client('gestor');user=self.client('morador')
        sid=self.call(manager,'spaces',self.space_data())[1]['id']
        payload={**self.payload(),'space_id':sid,'start':self.payload()['start'][:10]+'T10:00','end':self.payload()['end'][:10]+'T12:00'}
        status,result=self.call(user,'bookings',{**payload,'guests':6})
        self.assertEqual(status,422)
        self.assertFalse(next(c for c in result['checks'] if c['name']=='Capacidade')['ok'])
        status,result=self.call(user,'bookings',payload)
        self.assertEqual(status,200);self.assertEqual(result['status'],'pendente')
        self.assertEqual(self.call(manager,'decision',{'id':result['id'],'status':'confirmada','reason':'Regras do salão conferidas.'})[0],200)
        self.assertEqual(self.call(manager,'blocks',{'space_id':sid,'start':payload['start'],'end':payload['end'],'reason':'Conflito de manutenção.'})[0],409)

    def test_automatic_confirmation_is_not_tied_to_pool_id(self):
        manager=self.client('gestor');user=self.client('morador')
        sid=self.call(manager,'spaces',self.space_data(approval=False))[1]['id']
        payload={**self.payload(),'space_id':sid}
        status,result=self.call(user,'bookings',payload)
        self.assertEqual(status,200);self.assertEqual(result['status'],'confirmada')


    def test_new_space_creation_does_not_create_fake_events(self):
        manager=self.client('gestor')
        before=self.call(manager,'state')[1]
        payload={'name':'Sala de leitura','description':'Espaço silencioso para estudo.',
                 'capacity':4,'opens':8,'closes':20,'fee':0,'notice':2,'monthly_limit':4,'approval':False}
        self.assertEqual(self.call(manager,'spaces',payload)[0],200)
        after=self.call(manager,'state')[1]
        self.assertEqual(after['bookings'],before['bookings'])
        self.assertEqual(after['blocks'],before['blocks'])
