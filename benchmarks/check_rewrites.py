#!/usr/bin/env python3
"""Checks that the rewritten queries give the original results for every value
of their parameters, not only for the values the workload happens to use.

compare_results.sh proves the 30 optimized queries return what the originals
return, as written: doctor 217, 30 June 2026, the year 2026, page 4,001. A
rewrite can still be wrong for another doctor, day, year or page - a date on a
month or year boundary, a day with no appointments, a page that cuts a tie -
and the scalability phase runs these queries on other data. This script takes
each rewritten query from sql/06_optimized_queries.sql and its original from
queries/workload_30.sql, replaces only the literal values (each must occur
exactly once), and runs both over a sweep of values:

  R02  doctor                    300 doctors
  R05  doctor and day            300 pairs: busy days, empty days, month and year boundaries
  R08, R12  year                 2024-2027 (2024 and 2027 hold no appointments)
  R15, R20  month                every month from January 2025 to July 2026
  R17  start date                five dates, the last one after the data ends
  R18  city and language         10 cities x 8 languages
  R22  page                      50 offsets, from the second row to the last page
  W02  day                       26 days around 1 July 2026, inside BEGIN ... ROLLBACK

Both queries run on the same database, so only the SQL differs. Verdicts as in
compare_results.sh: IDENTICAL (same rows, same order), SAME (same rows; order
differs only among rows with equal sort keys), TIES (same sort keys, another of
several tied rows at a LIMIT/OFFSET edge; only for a query with LIMIT or OFFSET),
DIFFERENT (anything else). R13 is left out: its original is wrong on purpose
(docs/04, section 3).

The seed data has no appointment starting between 22:00 and 10:00, so a day
boundary moved by a few hours would not show here. The day and month rewrites
do not depend on the data: DATE(x) = d holds exactly when d <= x < d + 1 in the
session time zone, which is what the rewrites test.

    python3 benchmarks/check_rewrites.py [database]      (default: practo)

Exit status 0 when no rewrite gives a DIFFERENT result.
"""
import datetime
import pathlib
import random
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = sys.argv[1] if len(sys.argv) > 1 else 'practo'


def queries(path):
    """The queries of a workload file, split the way run_benchmark.sh splits them."""
    out, qid = {}, None
    for line in (ROOT / path).read_text().splitlines():
        if line.startswith('-- @'):
            qid = line.split()[1][1:]
            qid = qid if qid[:1] in 'RW' and qid[1:].isdigit() else None
            if qid:
                out[qid] = []
        elif qid and not line.startswith('--'):
            out[qid].append(line)
    return {q: '\n'.join(v).strip() for q, v in out.items()}


ORIGINAL = queries('queries/workload_30.sql')
REWRITE = queries('sql/06_optimized_queries.sql')


def fill(sql, replacements, where):
    for old, new in replacements:
        if sql.count(old) != 1:
            sys.exit('%s: expected %r exactly once' % (where, old))
        sql = sql.replace(old, new)
    return sql


def psql(sql):
    r = subprocess.run(['psql', '-d', DB, '-X', '-q', '-A', '-t', '-F', '|', '-v', 'ON_ERROR_STOP=1'],
                       input='SET search_path TO practo;\n' + sql, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('psql failed: %s' % r.stderr.strip())
    return r.stdout


def run_cases(cases, write=False):
    """Runs the original and the rewrite of every case in one session; returns their rows."""
    script = []
    for n, (orig, new) in enumerate(cases):
        for side, sql in (('o', orig), ('r', new)):
            script.append('\\echo ===%d %s' % (n, side))
            if write:   # the rows the statement changed, after the change, then undo it
                script.append('BEGIN;\n%s;\nSELECT appointment_id, slot_start FROM appointments t '
                              'WHERE t.xmin::text = (txid_current() %% 4294967296)::text ORDER BY 1;\nROLLBACK;'
                              % sql.rstrip(';'))
            else:
                script.append(sql if sql.endswith(';') else sql + ';')
    rows, key = {}, None
    for line in psql('\n'.join(script)).splitlines():
        if line.startswith('==='):
            n, side = line[3:].split()
            key = (int(n), side)
            rows[key] = []
        elif line:
            rows[key].append(line)
    return [(rows[(n, 'o')], rows[(n, 'r')]) for n in range(len(cases))]


def verdict(orig, new, keys, limited):
    """TIES only where a LIMIT or OFFSET can cut through rows with equal sort keys;
    anywhere else, other rows under the same sort keys are a DIFFERENT result."""
    pick = lambda rows: [tuple(r.split('|')[k - 1] for k in keys) for r in rows]
    if orig == new:
        return 'IDENTICAL'
    if len(orig) == len(new) and keys and pick(orig) == pick(new):
        if sorted(orig) == sorted(new):
            return 'SAME'
        if limited:
            return 'TIES'
    return 'DIFFERENT'


def months():
    d = datetime.date(2025, 1, 1)
    while d <= datetime.date(2026, 7, 1):
        nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        yield d, nxt
        d = nxt


def main():
    rng = random.Random(2026)
    day = datetime.timedelta(days=1)
    checks = []    # (query id, ORDER BY columns, [(label, original sql, rewritten sql)], write?)

    doctors = [int(x) for x in psql('SELECT doctor_id FROM doctors ORDER BY 1;').split()]
    picked = [217] + rng.sample([d for d in doctors if d != 217], 299)
    checks.append(('R02', [1, 2], [('doctor %d' % d,
                   fill(ORIGINAL['R02'], [('sc.doctor_id = 217', 'sc.doctor_id = %d' % d)], 'R02'),
                   fill(REWRITE['R02'], [('sc.doctor_id = 217', 'sc.doctor_id = %d' % d)], 'R02'))
                   for d in picked], False))

    busy = [l.split('|') for l in psql(
        "SELECT setseed(0.26); SELECT doctor_id, (slot_start AT TIME ZONE 'Asia/Kolkata')::date FROM appointments "
        "ORDER BY random() LIMIT 250;").split() if '|' in l]
    edges = [datetime.date(2025, 1, 1), datetime.date(2025, 1, 31), datetime.date(2025, 2, 28),
             datetime.date(2025, 12, 31), datetime.date(2026, 1, 1), datetime.date(2026, 6, 30),
             datetime.date(2026, 7, 1), datetime.date(2026, 7, 15), datetime.date(2026, 7, 16)]
    pairs = [(int(d), datetime.date.fromisoformat(s)) for d, s in busy]
    pairs += [(rng.choice(doctors), e) for e in edges for _ in range(3)]
    pairs += [(rng.choice(doctors), datetime.date(2025, 1, 1) + rng.randrange(600) * day) for _ in range(23)]
    checks.append(('R05', [3], [('doctor %d on %s' % (d, s),
                   fill(ORIGINAL['R05'], [('a.doctor_id = 16', 'a.doctor_id = %d' % d),
                                          ("DATE(a.slot_start) = '2026-06-30'", "DATE(a.slot_start) = '%s'" % s)], 'R05'),
                   fill(REWRITE['R05'], [('a.doctor_id = 16', 'a.doctor_id = %d' % d),
                                         ("a.slot_start >= '2026-06-30' AND a.slot_start < '2026-07-01'",
                                          "a.slot_start >= '%s' AND a.slot_start < '%s'" % (s, s + day))], 'R05'))
                   for d, s in pairs], False))

    for qid, keys, col in (('R08', [3], 'a.slot_start'), ('R12', [1, 2], 'slot_start')):
        checks.append((qid, keys, [('year %d' % y,
                       fill(ORIGINAL[qid], [('EXTRACT(YEAR FROM %s) = 2026' % col,
                                             'EXTRACT(YEAR FROM %s) = %d' % (col, y))], qid),
                       fill(REWRITE[qid], [("%s >= '2026-01-01' AND %s < '2027-01-01'" % (col, col),
                                            "%s >= '%d-01-01' AND %s < '%d-01-01'" % (col, y, col, y + 1))], qid))
                       for y in range(2024, 2028)], False))

    checks.append(('R15', [1, 3], [(m.strftime('%Y-%m'),
                   fill(ORIGINAL['R15'], [("p.issued_at >= '2026-06-01' AND p.issued_at < '2026-07-01'",
                                           "p.issued_at >= '%s' AND p.issued_at < '%s'" % (m, nxt))], 'R15'),
                   fill(REWRITE['R15'], [("month = DATE '2026-06-01'", "month = DATE '%s'" % m)], 'R15'))
                   for m, nxt in months()], False))

    checks.append(('R17', [1, 2], [('from %s' % s,
                   fill(ORIGINAL['R17'], [("a.slot_start >= '2026-01-01'", "a.slot_start >= '%s'" % s)], 'R17'),
                   fill(REWRITE['R17'], [("a.slot_start >= '2026-01-01'", "a.slot_start >= '%s'" % s)], 'R17'))
                   for s in ('2025-01-01', '2025-06-15', '2026-01-01', '2026-06-30', '2026-07-16')], False))

    languages = psql("SELECT DISTINCT l FROM doctors, unnest(languages) l ORDER BY 1;").split()
    checks.append(('R18', [4], [('city %d, %s' % (c, lang),
                   fill(ORIGINAL['R18'], [('c.city_id = 5', 'c.city_id = %d' % c),
                                          ("'Tamil' = ANY", "'%s' = ANY" % lang)], 'R18'),
                   fill(REWRITE['R18'], [('c.city_id = 5', 'c.city_id = %d' % c),
                                         ("'Tamil' = ANY", "'%s' = ANY" % lang)], 'R18'))
                   for c in range(1, 11) for lang in languages], False))

    checks.append(('R20', [1], [(m.strftime('%Y-%m'),
                   fill(ORIGINAL['R20'], [("slot_start::date BETWEEN '2026-06-01' AND '2026-06-30'",
                                           "slot_start::date BETWEEN '%s' AND '%s'" % (m, nxt - day))], 'R20'),
                   fill(REWRITE['R20'], [("slot_start >= '2026-06-01' AND slot_start < '2026-07-01'",
                                          "slot_start >= '%s' AND slot_start < '%s'" % (m, nxt))], 'R20'))
                   for m, nxt in months()], False))

    # R22: the bookmark of page n is the last row of page n-1 in the order the rewrite defines
    total = int(psql('SELECT count(*) FROM appointments;'))
    offsets = sorted({1, 25, 100, 1000, 10000, 50000, 100000, 120000, total - 25, total - 1}
                     | {rng.randrange(1, total) for _ in range(40)})
    marks = dict((int(o), (ts, int(i))) for o, ts, i in (l.split('|') for l in psql(
        'SELECT rn, slot_start, appointment_id FROM (SELECT slot_start, appointment_id, row_number() OVER '
        '(ORDER BY slot_start DESC, appointment_id DESC) AS rn FROM appointments) x WHERE rn IN (%s);'
        % ', '.join(map(str, offsets))).splitlines()))
    checks.append(('R22', [5], [('offset %d' % o,
                   fill(ORIGINAL['R22'], [('OFFSET 100000', 'OFFSET %d' % o)], 'R22'),
                   fill(REWRITE['R22'], [("(TIMESTAMPTZ '2025-07-08 12:15:00+05:30', 50125)",
                                          "(TIMESTAMPTZ '%s', %d)" % marks[o])], 'R22'))
                   for o in offsets], False))

    days = [datetime.date(2026, 6, 25) + k * day for k in range(26)]
    checks.append(('W02', [2], [(str(s),
                   fill(ORIGINAL['W02'], [("slot_start >= '2026-07-01' AND slot_start < '2026-07-02'",
                                           "slot_start >= '%s' AND slot_start < '%s'" % (s, s + day))], 'W02'),
                   fill(REWRITE['W02'], [("slot_start >= '2026-07-01' AND slot_start < '2026-07-02'",
                                          "slot_start >= '%s' AND slot_start < '%s'" % (s, s + day))], 'W02'))
                   for s in days], True))

    print('Rewritten queries against their originals in %s, for every value swept' % DB)
    print('  %-5s %6s %10s %6s %6s %10s   %s' % ('query', 'cases', 'IDENTICAL', 'SAME', 'TIES', 'DIFFERENT', 'rows compared'))
    failed = False
    for qid, keys, cases, write in checks:
        results = run_cases([(o, r) for _, o, r in cases], write)
        limited = re.search(r'\b(LIMIT|OFFSET)\b', ORIGINAL[qid], re.I) is not None
        counts = {'IDENTICAL': 0, 'SAME': 0, 'TIES': 0, 'DIFFERENT': 0}
        bad = []
        for (label, _, _), (orig, new) in zip(cases, results):
            v = verdict(orig, new, keys, limited)
            counts[v] += 1
            if v == 'DIFFERENT':
                bad.append('%s: %d rows from the original, %d from the rewrite' % (label, len(orig), len(new)))
        print('  %-5s %6d %10d %6d %6d %10d   %s' % (qid, len(cases), counts['IDENTICAL'], counts['SAME'],
              counts['TIES'], counts['DIFFERENT'], '{:,}'.format(sum(len(o) for o, _ in results))))
        for b in bad[:3]:
            print('        DIFFERENT for ' + b)
        failed |= bool(bad)
    print('No rewrite changes a result.' if not failed else 'Some rewrites change a result (above).')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
