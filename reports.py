"""RF15: indicadores por período de utilização, com exportação CSV."""
import csv
import io
from datetime import date, datetime, time, timedelta


def merge(intervals):
    result = []
    for start, end in sorted((a, b) for a, b in intervals if b > a):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def hours(intervals):
    return sum((b-a).total_seconds() / 3600 for a, b in merge(intervals))


def intersect(left, right):
    return [(max(a, c), min(b, d)) for a, b in left for c, d in right
            if max(a, c) < min(b, d)]


def build(c, params, today):
    first = date.fromisoformat(params.get('from', today.replace(day=1).isoformat()))
    last = date.fromisoformat(params.get('to', today.isoformat()))
    if last == date.max:
        raise ValueError('Data final fora do intervalo suportado.')
    days = (last-first).days + 1
    if not 1 <= days <= 366:
        raise ValueError('Escolha um período de 1 a 366 dias, com fim após o início.')
    sid = params.get('space_id', '')
    if sid and (not sid.isdigit() or int(sid) < 1):
        raise ValueError('Espaço inválido.')
    spaces = [dict(s) for s in c.execute('SELECT * FROM spaces ORDER BY id')
              if not sid or s['id'] == int(sid)]
    if sid and not spaces:
        raise ValueError('Espaço inexistente.')
    start, end = datetime.combine(first, time()), datetime.combine(last+timedelta(days=1), time())
    rows = []
    for s in spaces:
        bookings = [dict(b) for b in c.execute('''SELECT * FROM bookings
            WHERE space_id=? AND start<? AND end>?''',
            (s['id'], end.isoformat(timespec='minutes'), start.isoformat(timespec='minutes')))]
        opening = [(datetime.combine(first+timedelta(days=i), time(s['opens'])),
                    datetime.combine(first+timedelta(days=i), time(s['closes']))) for i in range(days)]
        blocks = merge([(datetime.fromisoformat(b['start']),
                         min(datetime.fromisoformat(b['end']), datetime.fromisoformat(b['removed_at']))
                         if b['removed_at'] else datetime.fromisoformat(b['end']))
                        for b in c.execute('SELECT * FROM blocks WHERE space_id=?', (s['id'],))])
        blocked = intersect(opening, blocks)
        available = hours(opening) - hours(blocked)
        confirmed = merge([(datetime.fromisoformat(b['start']), datetime.fromisoformat(b['end']))
                           for b in bookings if b['status'] == 'confirmada'])
        in_opening = intersect(confirmed, opening)
        occupied = max(0, hours(in_opening) - hours(intersect(in_opening, blocks)))
        waits = []
        for b in bookings:
            events = c.execute('SELECT action,created FROM audit WHERE booking_id=? ORDER BY id', (b['id'],)).fetchall()
            created = next((e['created'] for e in events if e['action'] == 'criação'), None)
            approved = next((e['created'] for e in events if e['action'] == 'confirmada'), None)
            if created and approved:
                waits.append(max(0, (datetime.fromisoformat(approved)-datetime.fromisoformat(created)).total_seconds()/60))
        incidents = c.execute('''SELECT count(*) FROM incidents WHERE space_id=?
            AND created>=? AND created<?''', (s['id'], start.isoformat(), end.isoformat())).fetchone()[0]
        undated = c.execute('SELECT count(*) FROM incidents WHERE space_id=? AND created IS NULL', (s['id'],)).fetchone()[0]
        counts = {status: sum(b['status'] == status for b in bookings)
                  for status in ('confirmada', 'pendente', 'cancelada', 'rejeitada')}
        rows.append({'space_id': s['id'], 'space': s['name'], 'reservations': len(bookings), **counts,
                     'confirmed_hours': round(occupied, 2), 'available_hours': round(available, 2),
                     'occupancy_pct': round(100*occupied/available, 2) if available > 0 else 0,
                     'approval_samples': len(waits),
                     'avg_approval_minutes': round(sum(waits)/len(waits), 2) if waits else None,
                     'incidents': incidents, 'undated_incidents': undated})
    return {'from': first.isoformat(), 'to': last.isoformat(), 'spaces': rows,
            'totals': {key: sum(row[key] for row in rows) for key in
                       ('reservations', 'confirmada', 'pendente', 'cancelada', 'rejeitada', 'incidents', 'undated_incidents')}}


def to_csv(report):
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    fields = ('space', 'reservations', 'confirmada', 'pendente', 'cancelada', 'rejeitada',
              'confirmed_hours', 'available_hours', 'occupancy_pct', 'avg_approval_minutes',
              'approval_samples', 'incidents', 'undated_incidents')
    writer.writerow(['Início', 'Fim', 'Espaço', 'Reservas', 'Confirmadas', 'Pendentes', 'Canceladas',
                     'Rejeitadas', 'Horas confirmadas', 'Horas disponíveis', 'Ocupação (%)',
                     'Tempo médio de aprovação (min)', 'Aprovações medidas', 'Ocorrências no período',
                     'Ocorrências sem data (fora do período)'])
    def safe(value):
        return "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
    for row in report['spaces']:
        writer.writerow([report['from'], report['to'], *(safe(row[k]) for k in fields)])
    return ('\ufeff' + output.getvalue()).encode('utf-8')
