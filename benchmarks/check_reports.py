#!/usr/bin/env python3
"""Checks the timings quoted in the reports against the raw result files.

The brief: "Every number in your reports must match the raw plan files in
benchmarks/results/." This script reads the tables of the reports and
recomputes each number from the files it came from:

  docs/03_baseline_timings.md     the three runs, average and total of all 30 queries
                                  (baseline/<id>.txt, baseline/summary.csv); the averages
                                  and scan types of section 3; the share of R19
  docs/04_optimization_report.md  the headline table; before, after, improvement and
                                  speed-up of all 30 queries and the total; the numbers in
                                  the plan excerpts of section 5 (run 3 of each cited
                                  file); the indexes-only comparison; write overhead, from
                                  the plans and the pgbench and refresh logs; index sizes;
                                  the rejected rewrites
  README.md                       our headline numbers

    python3 benchmarks/check_reports.py

Exit status 0 when every number matches.
"""
import csv
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / 'benchmarks' / 'results'
IDS = ['R%02d' % i for i in range(1, 26)] + ['W%02d' % i for i in range(1, 6)]
GROUPS = {   # the query groups of the brief's Phase 5 targets, as the README names them
    'real-time screen': ['R02', 'R03', 'R04', 'R05', 'R06', 'R07', 'R23', 'W01', 'W02', 'W03'],
    'interactive query': ['R01', 'R11', 'R13', 'R18', 'R21', 'R22', 'W05'],
    'report': ['R08', 'R09', 'R10', 'R12', 'R14', 'R15', 'R16', 'R17', 'R19', 'R20', 'R24', 'R25'],
}


class Checker:
    def __init__(self):
        self.checked, self.wrong = 0, []

    def equal(self, where, stated, actual):
        self.checked += 1
        if stated != actual:
            self.wrong.append('%s: the report says %s, the files give %s' % (where, stated, actual))

    def true(self, where, condition, problem):
        self.checked += 1
        if not condition:
            self.wrong.append('%s: %s' % (where, problem))

    def section(self, title):
        print('  %-58s' % title, end='')
        self._mark = (self.checked, len(self.wrong))

    def done(self):
        n, w = self.checked - self._mark[0], len(self.wrong) - self._mark[1]
        print('%5d numbers   %s' % (n, 'ok' if not w else '%d WRONG' % w))


def num(text):
    """'**6,747.052**' -> '6747.052' (kept as text, so the printed precision is compared too)."""
    return text.replace('*', '').replace(',', '').replace('ms', '').strip()


def f3(x):
    return '%.3f' % x


def runs(phase, qid):
    """The Execution Time of every run in a raw plan file, as printed."""
    text = (RESULTS / phase / ('%s.txt' % qid)).read_text()
    return re.findall(r'Execution Time: ([\d.]+) ms', text)


def summary(phase):
    with open(RESULTS / phase / 'summary.csv', newline='') as f:
        return {r['query_id']: r for r in csv.DictReader(f)}


def section(text, heading):
    """The text of one '## ' section, from its heading to the next one."""
    start = text.index('\n## ' + heading)
    end = text.find('\n## ', start + 1)
    return text[start:end if end > 0 else len(text)]


def table_rows(text, first_cell):
    """Rows of the markdown tables whose first cell matches first_cell, split into cells."""
    out = []
    for line in text.splitlines():
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            m = re.match(first_cell, cells[0])
            if m:
                out.append((m, cells))
    return out


def improvement(before, after):
    return '%.1f%%' % ((float(before) - float(after)) / float(before) * 100)


def speedup(before, after, stated):
    """Stated as '7.3x' below ten and as a whole number ('105x', '1,334x') above."""
    x = float(before) / float(after)
    return ('%.1fx' % x) if '.' in stated else ('{:,}x'.format(int(x + 0.5)))


def check_baseline(c, base, base_runs):
    text = (ROOT / 'docs' / '03_baseline_timings.md').read_text()
    c.section('docs/03: runs, averages and total (section 2)')
    sec2 = section(text, '2. Timing table')
    rows = table_rows(sec2, r'(R\d\d|W\d\d): ')
    c.equal('docs/03 timing table', 'rows %d' % len(rows), 'rows %d' % len(IDS))
    for m, cells in rows:
        qid = m.group(1)
        stated = [num(x) for x in cells[2:6]]
        for i in range(3):
            c.equal('docs/03 %s run %d' % (qid, i + 1), stated[i], base_runs[qid][i])
        c.equal('docs/03 %s average' % qid, stated[3], base[qid]['avg_ms'])
        c.equal('summary.csv %s average of its runs' % qid, base[qid]['avg_ms'],
                f3(sum(map(float, base_runs[qid])) / 3))
    total = [num(x) for x in table_rows(sec2, r'\*\*Total')[0][1][2:6]]
    for i in range(3):
        c.equal('docs/03 total of run %d' % (i + 1), total[i],
                f3(sum(float(base_runs[q][i]) for q in IDS)))
    c.equal('docs/03 total of the averages', total[3], f3(sum(float(base[q]['avg_ms']) for q in IDS)))
    m = re.search(r'it alone is \*\*([\d.]+)%\*\* of the total\. The other 29 queries add up to ([\d,]+) ms', text)
    all_avg = sum(float(base[q]['avg_ms']) for q in IDS)
    c.equal('docs/03 share of R19', m.group(1), '%.1f' % (float(base['R19']['avg_ms']) / all_avg * 100))
    c.equal('docs/03 the other 29', m.group(2), '%d' % round(all_avg - float(base['R19']['avg_ms'])))
    c.done()

    c.section('docs/03: averages and scan types (section 3)')
    for m, cells in table_rows(section(text, '3. Scan type'), r'^(R\d\d|W\d\d)$'):
        qid = m.group(1)
        c.equal('docs/03 section 3 %s average' % qid, num(cells[2]), base[qid]['avg_ms'])
        # every table or index named in the scan-type cell must appear in the plan's scan nodes
        nodes = base[qid]['scan_nodes']
        for name in re.findall(r'`([a-z_0-9]+)`', cells[1]):
            c.true('docs/03 section 3 %s' % qid, name in nodes,
                   'names %s, which no scan in the last run\'s plan reads' % name)
    c.done()


def check_optimization(c, base, opt):
    text = (ROOT / 'docs' / '04_optimization_report.md').read_text()
    c.section('docs/04: before, after, improvement, speed-up (section 2)')
    sec2 = section(text, '2. The optimization table')
    rows = table_rows(sec2, r'^(R\d\d|W\d\d)$')
    c.equal('docs/04 optimization table', 'rows %d' % len(rows), 'rows %d' % len(IDS))
    for m, cells in rows:
        qid = m.group(1)
        before, after, impr = num(cells[1]), num(cells[4]), cells[5]
        c.equal('docs/04 %s before' % qid, before, base[qid]['avg_ms'])
        c.equal('docs/04 %s after' % qid, after, opt[qid]['avg_ms'])
        stated = re.match(r'\**(-?[\d.]+%)\**(?: \(([\d,.]+x)\))?', impr.replace('*', ''))
        c.equal('docs/04 %s improvement' % qid, stated.group(1), improvement(before, after))
        if stated.group(2):
            c.equal('docs/04 %s speed-up' % qid, stated.group(2), speedup(before, after, stated.group(2)))
    tb, ta = (f3(sum(float(s[q]['avg_ms']) for q in IDS)) for s in (base, opt))
    m, cells = table_rows(sec2, r'\*\*Total\*\*')[0]
    c.equal('docs/04 total before', num(cells[1]), tb)
    c.equal('docs/04 total after', num(cells[4]), ta)
    stated = re.match(r'(-?[\d.]+%) \(([\d.]+x)\)', cells[5].replace('*', ''))
    c.equal('docs/04 total improvement', stated.group(1), improvement(tb, ta))
    c.equal('docs/04 total speed-up', stated.group(2), speedup(tb, ta, stated.group(2)))
    c.done()

    c.section('docs/04: headline table')
    m = re.search(r'\| Total of the 30 average times \| \*\*([\d,.]+) ms\*\* \| \*\*([\d,.]+) ms\*\* '
                  r'\(([\d.]+%) less, (\d+)x faster\) \|', text)
    c.equal('docs/04 headline total before', num(m.group(1)), tb)
    c.equal('docs/04 headline total after', num(m.group(2)), ta)
    c.equal('docs/04 headline improvement', m.group(3), improvement(tb, ta))
    c.equal('docs/04 headline speed-up', m.group(4) + 'x', speedup(tb, ta, m.group(4)))
    m = re.search(r'\| Total without R19 [^|]*\| ([\d,.]+) ms \| ([\d,.]+) ms \(([\d.]+%) less\) \|', text)
    nb, na = (f3(sum(float(s[q]['avg_ms']) for q in IDS if q != 'R19')) for s in (base, opt))
    c.equal('docs/04 total without R19 before', num(m.group(1)), nb)
    c.equal('docs/04 total without R19 after', num(m.group(2)), na)
    c.equal('docs/04 total without R19 improvement', m.group(3), improvement(nb, na))
    m = re.search(r'\| Slowest query \| (\w+), ([\d,.]+) ms \| (\w+), ([\d,.]+) ms \|', text)
    for (qid, value), s, label in (((m.group(1), m.group(2)), base, 'before'), ((m.group(3), m.group(4)), opt, 'after')):
        slowest = max(IDS, key=lambda q: float(s[q]['avg_ms']))
        c.equal('docs/04 slowest %s' % label, '%s %s' % (qid, num(value)), '%s %s' % (slowest, s[slowest]['avg_ms']))
    c.done()

def run_text(phase, qid, n):
    """The plan of run n in a raw plan file."""
    text = (RESULTS / phase / ('%s.txt' % qid)).read_text()
    m = re.search(r'=== %s run %d ===\n(.*?)(?=\n=== |\Z)' % (qid, n), text, re.S)
    return m.group(1)


def avg(values, digits=3):
    return '%.*f' % (digits, sum(values) / len(values))


def check_excerpts(c, text, base, opt, idx):
    """Section 5 quotes run 3 of each plan: every timing, row count, cost and
    counter in a quoted block must be in that run of the file it names."""
    sec = section(text, '5. Before/after')
    c.section('docs/04: plan excerpts (section 5)')
    facts = re.compile(r'actual time=[\d.]+\.\.[\d.]+ rows=\d+|cost=[\d.]+\.\.[\d.]+ rows=\d+|'
                       r'Execution Time: [\d.]+ ms|Rows Removed by Filter: \d+|Heap Fetches: \d+|'
                       r'Disk: \d+kB|Timing: Generation [^\n]*')
    for sub in sec.split('\n### ')[1:]:
        qid = sub[:3]
        m = re.match(r'\w+ - [^(]*\(([\d,.]+) → ([\d,.]+) ms\)', sub)
        c.equal('docs/04 section 5 %s heading before' % qid, num(m.group(1)), base[qid]['avg_ms'])
        c.equal('docs/04 section 5 %s heading after' % qid, num(m.group(2)), opt[qid]['avg_ms'])
        pos = 0
        for block in re.finditer(r'```\n(.*?)```', sub, re.S):
            lead = sub[pos:block.start()]
            pos = block.end()
            phase = 'baseline' if 'baseline/' in lead or ('Before' in lead and 'After' not in lead) else 'optimized'
            plan = run_text(phase, qid, 3)
            for fact in facts.findall(block.group(1)):
                c.true('docs/04 section 5 %s %s' % (qid, phase), fact in plan,
                       'quotes "%s", which is not in run 3 of %s/%s.txt' % (fact, phase, qid))
        # one-line "Before:" summaries: a filter count and a time in brackets
        for line in re.findall(r'^Before: (.*)$', sub, re.M):
            plan = run_text('baseline', qid, 3)
            for fact in re.findall(r'Rows Removed by Filter: \d+', line):
                c.true('docs/04 section 5 %s baseline' % qid, fact in plan, 'quotes "%s", not in run 3' % fact)
            for t in re.findall(r'\(([\d.]+) ms\)', line):
                c.true('docs/04 section 5 %s baseline' % qid, 'Execution Time: %s ms' % t in plan,
                       'quotes %s ms, not the time of run 3' % t)
    m = re.search(r'the original OFFSET query still took ([\d.]+) ms \(indexes-only run\)', sec)
    c.equal('docs/04 section 5 R22 indexes-only', m.group(1), idx['R22']['avg_ms'])
    m = re.search(r'read all (\d+) of doctor 16\'s appointments and threw away (\d+) \(([\d.]+) ms\)', sec)
    c.equal('docs/04 section 5 R05 indexes-only', m.group(3), idx['R05']['avg_ms'])
    plan = run_text('indexes_only', 'R05', 3)
    c.true('docs/04 section 5 R05 indexes-only', 'rows=%d ' % (int(m.group(1)) - int(m.group(2))) in plan
           and 'Rows Removed by Filter: %s' % m.group(2) in plan, 'the 151 read / 148 discarded are not in the plan')
    m = re.search(r'estimated cost fell from ([\d.]+) million to ([\d.]+) thousand', sec)
    cost = lambda phase: float(re.search(r'cost=[\d.]+\.\.([\d.]+)', run_text(phase, 'R19', 3)).group(1))
    c.equal('docs/04 section 5 R19 cost before', m.group(1), '%.1f' % (cost('baseline') / 1e6))
    c.equal('docs/04 section 5 R19 cost after', m.group(2), '%.1f' % (cost('optimized') / 1e3))
    c.done()


def check_levers(c, text, base, idx, opt):
    """Section 6: before, original SQL on the indexed database, optimized SQL."""
    c.section('docs/04: indexes alone vs rewrites (section 6)')
    for m, cells in table_rows(section(text, '6. Which lever'), r'^((?:[RW]\d\d(?:, )?)+)$'):
        ids = m.group(1).split(', ')
        cols = [[num(v.replace('(wrong)', '').replace('(right)', '')) for v in cell.split(', ')] for cell in cells[1:4]]
        for qid, b, i, o in zip(ids, *cols):
            digits = len(b.split('.')[1]) if '.' in b else 0     # the before column is sometimes rounded
            c.equal('docs/04 section 6 %s before' % qid, b, '%.*f' % (digits, float(base[qid]['avg_ms'])))
            c.equal('docs/04 section 6 %s indexes only' % qid, i, idx[qid]['avg_ms'])
            c.equal('docs/04 section 6 %s optimized' % qid, o, opt[qid]['avg_ms'])
    c.done()


def check_writes(c, text, base, opt):
    sec = section(text, '7. Write overhead')
    wo = RESULTS / 'write_overhead'
    c.section('docs/04: write overhead (section 7)')
    for m, cells in table_rows(sec, r'^(W\d\d) '):
        c.equal('docs/04 section 7.1 %s before' % m.group(1), num(cells[1]), base[m.group(1)]['avg_ms'])
        c.equal('docs/04 section 7.1 %s after' % m.group(1), num(cells[2]), opt[m.group(1)]['avg_ms'])
    # 7.2 W01: the INSERT latency in pgbench, first vs second statement in a connection, the outlier
    insert = lambda f: re.search(r'^\s*([\d.]+)\s+\d+\s+INSERT', (wo / f).read_text(), re.M).group(1)
    m = re.search(r'averages \*\*([\d.]+) ms without the new indexes and ([\d.]+) ms with them\*\*', sec)
    c.equal('docs/04 section 7.2 pgbench INSERT without', m.group(1), insert('W01_practo_base.txt'))
    c.equal('docs/04 section 7.2 pgbench INSERT with', m.group(2), insert('W01_practo.txt'))
    trials = re.findall(r'^(\w+)\s+trial \d+\s+1st INSERT\s+([\d.]+) ms\s+2nd INSERT\s+([\d.]+) ms',
                        (wo / 'W01_first_vs_second_in_connection.txt').read_text(), re.M)
    rng = lambda db, k: '%.2f-%.2f' % (min(float(t[k]) for t in trials if t[0] == db),
                                       max(float(t[k]) for t in trials if t[0] == db))
    m = re.search(r'the 1st insert took ([\d.-]+) ms without and ([\d.-]+) ms with the new indexes; '
                  r'the 2nd took ([\d.-]+) ms and ([\d.-]+) ms', sec)
    for stated, (db, k) in zip(m.groups(), [('practo_base', 1), ('practo', 1), ('practo_base', 2), ('practo', 2)]):
        c.equal('docs/04 section 7.2 first/second insert %s' % db, stated, rng(db, k))
    m = re.search(r'in run 5 the foreign-key check on `patients` alone took ([\d.]+) ms', sec)
    c.true('docs/04 section 7.2 outlier', re.search(r'fk_appt_patient[^\n]*time=%s ' % re.escape(m.group(1)),
                                                     run_text('optimized', 'W01', 5)) is not None,
           'the %s ms trigger time is not in run 5' % m.group(1))
    m = re.search(r'the median is ([\d.]+) ms', sec)
    c.equal('docs/04 section 7.2 W01 median', m.group(1), opt['W01']['median_ms'])
    # 7.3 W04: finding the rows (the scan under the Update node) and writing them (the rest of the node)
    for label, phase, n in (('Before', 'baseline', 3), ('After', 'optimized', 5)):
        find, write = [], []
        for i in range(1, n + 1):
            plan = run_text(phase, 'W04', i)
            update = float(re.search(r'^Update on appointments .*actual time=[\d.]+\.\.([\d.]+)', plan, re.M).group(1))
            scan = float(re.search(r'^\s+->\s+.*actual time=[\d.]+\.\.([\d.]+)', plan, re.M).group(1))
            find.append(scan)
            write.append(update - scan)
        m = re.search(r'\| %s \(avg of %d\) \| ([\d.]+) ms [^|]*\| ([\d.]+) ms \| ([\d.]+) ms \|' % (label, n), sec)
        c.equal('docs/04 section 7.3 W04 %s finding' % label, m.group(1), avg(find))
        c.equal('docs/04 section 7.3 W04 %s writing' % label, m.group(2), avg(write))
        c.equal('docs/04 section 7.3 W04 %s total' % label, m.group(3), (base if phase == 'baseline' else opt)['W04']['avg_ms'])
        if label == 'Before':
            write_before = float(m.group(2))
        else:
            per_row = re.search(r'Writing became \*\*(\d+)% more expensive per row\*\*', sec).group(1)
            c.equal('docs/04 section 7.3 W04 per-row cost', per_row, '%d' % round((float(m.group(2)) / write_before - 1) * 100))
    latency = lambda f: re.search(r'latency average = ([\d.]+) ms', (wo / f).read_text()).group(1)
    m = re.search(r'W04 went from ([\d.]+) to ([\d.]+) ms per execution', sec)
    c.equal('docs/04 section 7.3 pgbench W04', m.groups(), (latency('W04_practo_base.txt'), latency('W04_practo.txt')))
    m = re.search(r'`pgbench` W02 ([\d.]+) → ([\d.]+) ms and W05 ([\d.]+) → ([\d.]+) ms', sec)
    c.equal('docs/04 section 7.3 pgbench W02, W05', m.groups(),
            tuple(latency(f) for f in ('W02_practo_base.txt', 'W02_practo.txt', 'W05_practo_base.txt', 'W05_practo.txt')))
    # 7.4 refresh of the summary tables: the log alternates the two views
    times = [float(t) for t in re.findall(r'Time: ([\d.]+) ms', (wo / 'mv_refresh.txt').read_text())]
    for name, ts in (('doctor_rating_summary', times[0::2]), ('monthly_medicine_by_specialization', times[1::2])):
        m = re.search(r'`%s` ([\d.]+)-([\d.]+) ms \(avg ([\d.]+)\)' % name, sec)
        c.equal('docs/04 section 7.4 %s' % name, m.groups(), ('%.1f' % min(ts), '%.1f' % max(ts), avg(ts, 1)))
    c.done()


def check_storage(c, text):
    """Section 8 against index_sizes.txt (pg_relation_size, in bytes and as pg_size_pretty prints them)."""
    sec = section(text, '8. Index storage')
    raw = (RESULTS / 'optimized' / 'index_sizes.txt').read_text()
    rows = {r[0]: r for r in (l.split('|') for l in raw.splitlines()) if len(r) == 6 and r[3].strip().isdigit()}
    rows = {k.strip(): [x.strip() for x in v] for k, v in rows.items()}
    after = dict(l.split(' | ') for l in raw.split('AFTER all benchmark passes')[1].strip().splitlines()[1:] if ' | ' in l)
    mib = lambda name: int(rows[name][3]) / 1048576
    c.section('docs/04: index storage (section 8)')
    for m, cells in table_rows(sec, r'^`(\w+)`$'):
        name = m.group(1)
        sizes = [x.strip().replace(',', '') for x in cells[3].split('+')]
        if rows[name][1] == 'materialized view':
            unique = [k for k, v in rows.items() if v[2] == name][0]
            c.equal('docs/04 section 8 %s' % name, sizes, [rows[name][4], rows[unique][4]])
        else:
            c.equal('docs/04 section 8 %s size' % name, sizes[0], rows[name][4])
            c.equal('docs/04 section 8 %s share of its table' % name, cells[4], rows[name][5] + '%')
            t = re.match(r'(\w+)(?: \(([\d.]+) MB\))?', cells[2])
            if t.group(2):
                digits = len(t.group(2).split('.')[1]) if '.' in t.group(2) else 0
                c.equal('docs/04 section 8 size of %s' % t.group(1), t.group(2), '%.*f' % (digits, mib(t.group(1))))
    m = re.search(r'\*\*Database size: (\d+) MB → (\d+) MB\*\* \(\+(\d+) MB, \+(\d+)%\)', sec)
    db = re.search(r'^\s*(\d+) MB\s*\|\s*(\d+) MB', raw, re.M)
    c.equal('docs/04 section 8 database size', (m.group(1), m.group(2)), (db.group(2), db.group(1)))
    c.equal('docs/04 section 8 growth', (m.group(3), m.group(4)),
            ('%d' % (int(db.group(1)) - int(db.group(2))), '%d' % round((int(db.group(1)) / int(db.group(2)) - 1) * 100)))
    m = re.search(r'covering indexes on `appointments` \(([\d.]+) MB together, (\d+)% of the table', sec)
    cover = ['ix_appt_doctor_slot', 'ix_appt_patient', 'ix_appt_slot_cover']
    c.equal('docs/04 section 8 covering indexes', m.groups(),
            ('%.1f' % sum(mib(n) for n in cover), '%d' % round(sum(mib(n) for n in cover) / mib('appointments') * 100)))
    m = re.search(r'`ix_appt_doctor_slot` had grown from ([^ ]+(?: kB)?) to ([^ ]+ [kM]B) and `ix_appt_patient` '
                  r'from ([^ ]+(?: kB)?) to ([^ ]+ [kM]B), while `ix_appt_slot_cover` grew only from ([^ ]+(?: kB)?) '
                  r'to ([^ ]+ [kM]B)', sec)
    for k, name in enumerate(cover):
        c.equal('docs/04 section 8 bloat of %s' % name, (m.group(2 * k + 1), m.group(2 * k + 2)),
                (rows[name][4], after[name].strip()))
    c.done()


def check_rejected(c, text, base, opt):
    sec = section(text, '9. Queries we could not')
    c.section('docs/04: queries not sped up, rejected rewrites (section 9)')
    for m, cells in table_rows(sec, r'^\*\*(R\d\d)\*\*'):
        qid = m.group(1)
        b, a = re.match(r'([\d.]+) → ([\d.]+) ms', cells[1]).groups()
        c.equal('docs/04 section 9 %s before' % qid, b, base[qid]['avg_ms'])
        c.equal('docs/04 section 9 %s after' % qid, a, opt[qid]['avg_ms'])
    for name, pattern in (('R24_groups_first', r'Find duplicate groups first, then join back: \*\*([\d.]+) ms\*\*'),
                          ('R24_lag_window', r'with `lag\(\)` over `ix_appt_doctor_slot`: \*\*([\d.]+) ms\*\*'),
                          ('R25_two_level_distinct', r'instead of `count\(DISTINCT\)`: \*\*([\d.]+) ms\*\*'),
                          ('R01_exists', r'`EXISTS` instead of `JOIN` \+ `DISTINCT`: \*\*([\d.]+) ms\*\*')):
        t = [float(x) for x in re.findall(r'Execution Time: ([\d.]+) ms', (RESULTS / 'rejected' / (name + '.txt')).read_text())]
        c.equal('docs/04 section 9 rejected %s' % name, re.search(pattern, sec).group(1), avg(t))
    m = re.search(r'\(R12, R17 and R25: (\d+) heap fetches per run', sec)
    for qid in ('R12', 'R17', 'R25'):
        c.true('docs/04 section 9 %s heap fetches' % qid,
               all('Heap Fetches: %s' % m.group(1) in run_text('optimized', qid, i) for i in range(1, 6)),
               'not every run shows Heap Fetches: %s' % m.group(1))
    c.done()


def check_readme(c, base, opt):
    text = (ROOT / 'README.md').read_text()
    c.section('README: our results')
    tb, ta = (f3(sum(float(s[q]['avg_ms']) for q in IDS)) for s in (base, opt))
    m = re.search(r'\| Total of the 30 average times \| ([\d,.]+) ms \| \*\*([\d,.]+) ms\*\* '
                  r'\(([\d.]+%) less, (\d+)x faster\) \|', text)
    c.equal('README total before', num(m.group(1)), tb)
    c.equal('README total after', num(m.group(2)), ta)
    c.equal('README improvement', m.group(3), improvement(tb, ta))
    c.equal('README speed-up', m.group(4) + 'x', speedup(tb, ta, m.group(4)))
    for label, ids in GROUPS.items():
        m = re.search(r'\| Slowest %s \([^)]*\) \| (\w+), ([\d,.]+) ms \| (\w+), ([\d,.]+) ms \|' % label, text)
        for (qid, value), s, when in (((m.group(1), m.group(2)), base, 'before'), ((m.group(3), m.group(4)), opt, 'after')):
            slowest = max(ids, key=lambda q: float(s[q]['avg_ms']))
            c.equal('README slowest %s %s' % (label, when), '%s %s' % (qid, num(value)),
                    '%s %s' % (slowest, s[slowest]['avg_ms']))
    m = re.search(r'\| Nightly batch \(W04\) \| ([\d.]+) ms \| ([\d.]+) ms \|', text)
    c.equal('README W04 before', m.group(1), base['W04']['avg_ms'])
    c.equal('README W04 after', m.group(2), opt['W04']['avg_ms'])
    c.done()


def main():
    base, opt, idx = summary('baseline'), summary('optimized'), summary('indexes_only')
    base_runs = {q: runs('baseline', q) for q in IDS}
    for q in IDS:
        if len(base_runs[q]) != 3:
            sys.exit('baseline/%s.txt has %d runs, not 3' % (q, len(base_runs[q])))
    c = Checker()
    print('Numbers in the reports against benchmarks/results/')
    check_baseline(c, base, base_runs)
    check_optimization(c, base, opt)
    text = (ROOT / 'docs' / '04_optimization_report.md').read_text()
    check_excerpts(c, text, base, opt, idx)
    check_levers(c, text, base, idx, opt)
    check_writes(c, text, base, opt)
    check_storage(c, text)
    check_rejected(c, text, base, opt)
    check_readme(c, base, opt)
    for w in c.wrong:
        print('  WRONG ' + w)
    print('%d numbers checked, %s' % (c.checked, 'all match.' if not c.wrong else '%d do not match.' % len(c.wrong)))
    sys.exit(1 if c.wrong else 0)


if __name__ == '__main__':
    main()
