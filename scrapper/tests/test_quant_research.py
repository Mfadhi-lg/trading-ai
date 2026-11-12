"""Offline synthetic counterexamples and hand-calculated cash ledgers."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D, Inexact, ROUND_DOWN, localcontext
from unittest import TestCase

from scrapper.research.quant.validation import Observation, Window, walk_forward
from scrapper.research.quant.backtest import Assumptions, Bar, Session, Target, run_backtest


def time(day, hour=0):
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


class WalkForwardTests(TestCase):
    def setUp(self):
        self.rows = [Observation(str(d), time(d), time(d), time(d+1), (D(d),), D(d+1))
                     for d in range(1, 9)]
        self.windows = [Window(time(1), time(4), time(6)), Window(time(2), time(6), time(8))]
        self.trains = []

    def fit(self, rows):
        self.trains.append(rows)
        return D(1)

    def predict(self, model, rows):
        self.assertTrue(all(not hasattr(r, 'label') for r in rows))
        return [r.features[0] + model for r in rows]

    def run_windows(self, **kwargs):
        args = dict(observations=self.rows, windows=self.windows, fit=self.fit,
                    predict=self.predict, score=lambda actual, predicted: {
                        'mae': sum((abs(x-y) for x,y in zip(actual,predicted)), D(0)) / len(actual)},
                    evaluation_at=time(10))
        args.update(kwargs)
        return walk_forward(**args)

    def test_purges_label_boundary_and_reports_all_windows(self):
        result = self.run_windows()
        self.assertEqual([r.train_keys for r in result], [('1','2'), ('2','3','4')])
        self.assertEqual([r.test_keys for r in result], [('4','5'), ('6','7')])
        self.assertEqual([r.metrics['mae'] for r in result], [0,0])
        self.assertEqual(len(self.trains), 2)

    def test_future_rows_do_not_change_earlier_predictions(self):
        before = self.run_windows()
        self.rows[-1] = replace(self.rows[-1], features=(D(999),), label=D(-999))
        self.assertEqual(before, self.run_windows())

    def test_later_test_features_hidden_from_earlier_predictions(self):
        predict = lambda model, rows: [rows[-1].features[0]] * len(rows)
        before = self.run_windows(predict=predict)
        self.rows[4] = replace(self.rows[4], features=(D(999),))
        after = self.run_windows(predict=predict)
        self.assertEqual(before[0].predictions[0], after[0].predictions[0])
        self.assertNotEqual(before[0].predictions[1], after[0].predictions[1])

    def test_contemporaneous_ranking_batch_and_empty_train_fail(self):
        self.rows.append(replace(self.rows[3],key='4-B',features=(D(40),)))
        sizes = []
        def rank(model, rows):
            sizes.append(len(rows))
            return [D(sorted(r.features[0] for r in rows).index(r.features[0])) for r in rows]
        result = self.run_windows(predict=rank)
        self.assertEqual(result[0].predictions[:2],(D(0),D(1)))
        self.assertEqual(sizes,[2,1,1,1])
        with self.assertRaises(ValueError):
            self.run_windows(observations=[r for r in self.rows if r.decision_at >= time(4)])

    def test_rejects_future_features_unavailable_labels_and_naive_times(self):
        for row in [replace(self.rows[0], features_available_at=time(2)),
                    replace(self.rows[0], decision_at=datetime(2026,1,1)),
                    replace(self.rows[3], label_available_at=time(11)),
                    replace(self.rows[0], features=(D('NaN'),)),
                    replace(self.rows[0], features=(1.0,))]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.run_windows(observations=[row] + [r for r in self.rows if r.key != row.key])

    def test_bad_windows_and_callbacks(self):
        for windows in [self.windows[:1], self.windows[::-1],
                        [self.windows[0], Window(time(1), time(5), time(8))]]:
            with self.assertRaises(ValueError): self.run_windows(windows=windows)
        with self.assertRaises(ValueError): self.run_windows(predict=lambda m, r: [])
        with self.assertRaises(ValueError): self.run_windows(score=lambda a,p: {'x':D('Infinity')})
        with self.assertRaises(ValueError): self.run_windows(observations=self.rows + self.rows[:1])


class BacktestTests(TestCase):
    def setUp(self):
        self.a = Assumptions(D(1000), D(1), D('.01'), D('.01'), D('.1'), 252,
                             'synthetic historical membership including exits', 'synthetic unadjusted prices')
        self.sessions = [Session(time(d,9),time(d,16),time(1),frozenset({'A'}),
                                (Bar('A',time(d,9),time(d,16),D(o),D(c),v,time(d-1,16)),))
                         for d,o,c,v in [(2,10,11,100),(3,12,10,100),(4,10,10,100)]]
        self.targets = [Target(time(1,16),time(1,16),{'A':20}), Target(time(2,16),time(2,16),{})]

    def run_test(self, **kwargs):
        args = dict(sessions=self.sessions, targets=self.targets, assumptions=self.a)
        args.update(kwargs)
        return run_backtest(**args)

    def test_reference_cash_costs_and_drawdown(self):
        result = self.run_test()
        # Buy 10 at 10.10: 101 notional + 1 brokerage + 1.01 fees.
        # Sell 10 at 11.88: 118.80 proceeds - 1 brokerage - 1.188 fees.
        self.assertEqual(result.marks[0].cash, D('896.99'))
        self.assertEqual(result.marks[0].equity, D('1006.99'))
        self.assertEqual(result.marks[-1].equity, D('1013.602'))
        self.assertEqual(result.total_return, D('.013602'))
        self.assertEqual(result.total_costs, D('6.398'))
        self.assertEqual(result.unfilled, ((time(2,9),'A',10),))
        self.assertEqual([f.shares for f in result.fills], [10,-10])
        self.assertEqual(result.max_drawdown, 0)
        self.assertGreater(result.annualized_volatility, 0)
        self.assertIsNotNone(result.sharpe_zero_rate)

    def test_drawdown_and_risk_reference(self):
        a = replace(self.a,brokerage_per_fill=D(0),fee_rate=D(0),slippage_rate=D(0),participation_rate=D(1))
        sessions = [replace(s,bars=(replace(s.bars[0],open=D(10),close=D(c)),))
                    for s,c in zip(self.sessions,[10,8,9])]
        result = self.run_test(sessions=sessions,targets=self.targets[:1],assumptions=a)
        self.assertEqual(result.total_return, D('-.02'))  # 20 shares, 800 cash
        self.assertEqual(result.max_drawdown,D('.04'))
        with localcontext() as ctx:
            ctx.prec = 34
            rs = [D(0), D('-.04'), D(980)/960-1]
            mean = sum(rs)/3
            vol = (sum((r-mean)**2 for r in rs)/2).sqrt()*D(252).sqrt()
            self.assertEqual(result.annualized_volatility, vol)

    def test_cash_limit_zero_volume_and_reproducibility(self):
        a = replace(self.a, initial_cash=D(25))
        result = self.run_test(assumptions=a)
        self.assertEqual(result.fills[0].shares,2)
        self.assertTrue(all(m.cash >= 0 for m in result.marks))
        self.assertEqual(result,self.run_test(assumptions=a))
        with localcontext() as ctx:
            ctx.prec = 6
            ctx.rounding = ROUND_DOWN
            ctx.traps[Inexact] = True
            self.assertEqual(result,self.run_test(assumptions=a))
        sessions = [replace(s,bars=(replace(s.bars[0],liquidity_volume=0),)) for s in self.sessions]
        result = self.run_test(sessions=sessions)
        self.assertEqual(result.fills,())
        self.assertEqual(result.total_return,0)
        self.assertIsNone(result.sharpe_zero_rate)

    def test_rejects_future_liquidity_estimate(self):
        s = self.sessions[0]
        sessions = [replace(s,bars=(replace(s.bars[0],liquidity_available_at=s.closed_at),))]+self.sessions[1:]
        with self.assertRaisesRegex(ValueError, 'liquidity estimate uses future'):
            self.run_test(sessions=sessions)

    def test_bankruptcy_and_zero_proceeds_terminal_exit(self):
        a = replace(self.a,brokerage_per_fill=D(0),fee_rate=D(0),slippage_rate=D(0),participation_rate=D(1))
        first, second, third = self.sessions
        first = replace(first,bars=(replace(first.bars[0],close=D(0)),))
        terminal = lambda s: replace(s,members=frozenset(),bars=(replace(s.bars[0],open=D(0),close=D(0)),))
        sessions = [first,terminal(second),terminal(third)]
        targets = [replace(self.targets[0],shares={'A':100})]
        result = self.run_test(sessions=sessions,targets=targets,assumptions=a)
        self.assertEqual(result.total_return,-1)
        self.assertEqual(result.max_drawdown,1)
        self.assertEqual([m.period_return for m in result.marks],[-1,0,0])
        targets.append(Target(time(2,16),time(2,16),{}))
        result = self.run_test(sessions=sessions,targets=targets,assumptions=a)
        self.assertEqual(result.fills[-1].shares,-100)
        self.assertEqual(result.fills[-1].price,0)
        self.assertEqual(result.marks[-1].holdings,())

    def test_delisted_asset_must_not_disappear(self):
        # Once removed from membership, an existing holding still needs valuation.
        sessions = [self.sessions[0],replace(self.sessions[1],members=frozenset(),bars=()),self.sessions[2]]
        with self.assertRaisesRegex(ValueError,'held asset price missing'):
            self.run_test(sessions=sessions,targets=self.targets[:1])
        sessions[1] = replace(sessions[1],bars=self.sessions[1].bars)
        result = self.run_test(sessions=sessions)
        self.assertEqual(result.fills[-1].shares,-10)

    def test_rejects_lookahead_membership_bad_bars_and_missing_session(self):
        for sessions in [self.sessions[::-1],
                         [replace(self.sessions[0],universe_available_at=time(2,10))]+self.sessions[1:],
                         [replace(self.sessions[0],bars=())]+self.sessions[1:],
                         [replace(self.sessions[0],bars=(replace(self.sessions[0].bars[0],open=D('NaN')),))]+self.sessions[1:]]:
            with self.assertRaises(ValueError): self.run_test(sessions=sessions)
        for target in [replace(self.targets[0],information_available_at=time(2)),
                       replace(self.targets[0],shares={'SURVIVOR_ONLY':1}),
                       replace(self.targets[0],shares={'A':-1}),
                       replace(self.targets[0],decision_at=time(5))]:
            with self.assertRaises(ValueError): self.run_test(targets=[target])
        with self.assertRaises(ValueError): self.run_test(targets=[self.targets[0],replace(self.targets[0],decision_at=time(1,17))])

    def test_open_at_decision_executes_later_and_future_prices_do_not_affect_fill(self):
        target = Target(time(2,9),time(2,9),{'A':1})
        result = self.run_test(targets=[target])
        self.assertEqual(result.fills[0].at,time(3,9))
        changed = self.sessions[:-1]+[replace(self.sessions[-1],bars=(replace(self.sessions[-1].bars[0],close=D(999)),))]
        self.assertEqual(result.fills,self.run_test(targets=[target],sessions=changed).fills)

    def test_invalid_assumptions(self):
        for a in [replace(self.a,fee_rate=D(-1)),replace(self.a,slippage_rate=D(1)),
                  replace(self.a,participation_rate=D(0)),replace(self.a,universe_provenance=''),
                  replace(self.a,initial_cash=1000.0)]:
            with self.assertRaises(ValueError): self.run_test(assumptions=a)

    def test_multiple_symbols_sells_fund_buys_and_order_is_deterministic(self):
        a = replace(self.a,initial_cash=D(100),brokerage_per_fill=D(0),fee_rate=D(0),
                    slippage_rate=D(0),participation_rate=D(1))
        sessions = [replace(s,members=frozenset({'A','B'}),bars=(
            replace(s.bars[0],open=D(10),close=D(10)),
            replace(s.bars[0],symbol='B',open=D(10),close=D(10)))) for s in self.sessions]
        targets = [replace(self.targets[0],shares={'B':10,'A':10}),
                   replace(self.targets[1],shares={'B':10})]
        result = self.run_test(sessions=sessions,targets=targets,assumptions=a)
        self.assertEqual([(f.symbol,f.shares) for f in result.fills],[('A',10),('A',-10),('B',10)])
        reversed_bars = [replace(s,bars=s.bars[::-1]) for s in sessions]
        targets[0] = replace(targets[0],shares={'A':10,'B':10})
        self.assertEqual(result,self.run_test(sessions=reversed_bars,targets=targets,assumptions=a))
