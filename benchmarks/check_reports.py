#!/usr/bin/env python3
"""Checks the timings quoted in the reports against the raw result files.

The brief: "Every number in your reports must match the raw plan files in
benchmarks/results/." This script reads the tables of the reports and
recomputes each number from the files it came from:

  docs/03_baseline_timings.md     the three runs, average and total of all 30 queries
                                  (baseline/<id>.txt, baseline/summary.csv); the averages
                                  and scan types of section 3; the share of R19
  docs/04_optimization_report.md  the headline table; before, after, improvement and
                                  speed-up of all 30 queries and the total
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
    base, opt = summary('baseline'), summary('optimized')
    base_runs = {q: runs('baseline', q) for q in IDS}
    for q in IDS:
        if len(base_runs[q]) != 3:
            sys.exit('baseline/%s.txt has %d runs, not 3' % (q, len(base_runs[q])))
    c = Checker()
    print('Numbers in the reports against benchmarks/results/')
    check_baseline(c, base, base_runs)
    check_optimization(c, base, opt)
    check_readme(c, base, opt)
    for w in c.wrong:
        print('  WRONG ' + w)
    print('%d numbers checked, %s' % (c.checked, 'all match.' if not c.wrong else '%d do not match.' % len(c.wrong)))
    sys.exit(1 if c.wrong else 0)


if __name__ == '__main__':
    main()
