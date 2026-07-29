"""Testy silnika equity management — czysta logika, bez bazy/sieci."""
from app.rules import (
    AccountRuntime, ChallengeConfig, DrawdownType, EquityTick, Phase, Status,
    BreachType, evaluate,
)


def cfg(**kw):
    base = dict(initial_balance=100_000, profit_target_pct=8,
                max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=2)
    base.update(kw)
    return ChallengeConfig(**base)


def fresh_rt(balance=100_000):
    return AccountRuntime(balance=balance, equity=balance, peak_equity=balance,
                          day_start_equity=balance, day_key="2026-05-29")


def tick(equity, balance=None, day="2026-05-29", open_pos=False, days=0):
    return EquityTick(equity=equity, balance=balance if balance is not None else equity,
                      open_pnl=round(equity-(balance if balance is not None else equity),2),
                      day_key=day, has_open_position=open_pos, days_elapsed=days)


def test_no_breach_within_limits():
    c, rt = cfg(), fresh_rt()
    r = evaluate(c, rt, tick(98_000))   # -2% < 5% daily
    assert not r.failed and not r.breaches
    assert rt.status == Status.ACTIVE


def test_daily_loss_breach():
    c, rt = cfg(), fresh_rt()
    # daily floor = 100k - 5% = 95k ; equity 94.9k -> breach
    r = evaluate(c, rt, tick(94_900))
    assert r.failed
    assert r.breaches[0][0] == BreachType.DAILY_LOSS
    assert rt.status == Status.FAILED


def test_static_max_drawdown_breach():
    c, rt = cfg(), fresh_rt()
    # nowy dzień -> baseline 90k, daily floor 85k; ale overall static floor=90k
    rt.day_key = "2026-05-28"; rt.day_start_equity = 100_000
    r = evaluate(c, rt, tick(89_500, day="2026-05-30"))
    # po zmianie dnia baseline=89.5k, daily nie złamane; overall static floor=90k -> equity 89.5k breach
    assert r.failed
    types = [b[0] for b in r.breaches]
    assert BreachType.MAX_DRAWDOWN in types


def test_trailing_drawdown_follows_peak():
    c = cfg(drawdown_type=DrawdownType.TRAILING)
    rt = fresh_rt()
    # equity rośnie do 110k (peak), trailing floor = 110k - 10% z 100k = 100k
    evaluate(c, rt, tick(110_000, day="2026-05-29"))
    assert rt.peak_equity == 110_000
    # spadek do 99.9k -> poniżej trailing floor 100k -> breach (mimo zysku!)
    r = evaluate(c, rt, tick(99_900))
    assert r.failed
    assert any(b[0] == BreachType.MAX_DRAWDOWN for b in r.breaches)


def test_daily_reset_on_new_day():
    c, rt = cfg(), fresh_rt()
    evaluate(c, rt, tick(97_000, day="2026-05-29"))
    assert rt.day_start_equity == 100_000
    # nowy dzień: baseline resetuje się do bieżącego equity
    evaluate(c, rt, tick(97_000, day="2026-05-30"))
    assert rt.day_start_equity == 97_000


def test_pass_requires_target_and_min_days():
    c, rt = cfg(min_trading_days=2), fresh_rt()
    # osiąga cel (108k balance) ale tylko 1 dzień handlowy -> jeszcze nie pass
    evaluate(c, rt, tick(108_000, balance=108_000, day="2026-05-29", open_pos=True))
    r = evaluate(c, rt, tick(108_000, balance=108_000, day="2026-05-29", open_pos=True))
    assert not r.passed_phase                    # ten sam dzień -> 1 trading day
    # drugi dzień handlowy + cel utrzymany -> pass
    r = evaluate(c, rt, tick(108_500, balance=108_500, day="2026-05-30", open_pos=True))
    assert r.passed_phase
    assert rt.status == Status.PASSED


def test_failed_account_is_noop():
    c, rt = cfg(), fresh_rt()
    rt.status = Status.FAILED
    r = evaluate(c, rt, tick(50_000))
    assert not r.breaches and not r.failed       # nie przetwarza zamkniętych kont


def test_consistency_rule_blocks_pass():
    # reguła 40%: pojedynczy dzień nie może dać >40% całego zysku
    c = cfg(min_trading_days=1, consistency_pct=40)
    rt = fresh_rt()
    evaluate(c, rt, tick(100_000, balance=100_000, day="2026-05-29", open_pos=True))  # start dnia
    # cały zysk +8% zrobiony w JEDNYM dniu => best-day = 100% zysku > 40% => brak pass
    r = evaluate(c, rt, tick(108_000, balance=108_000, day="2026-05-29", open_pos=True))
    assert r.metrics["consistency_ok"] is False
    assert not r.passed_phase


def test_consistency_ok_when_spread():
    c = cfg(min_trading_days=2, consistency_pct=40)
    rt = fresh_rt()
    evaluate(c, rt, tick(100_000, balance=100_000, day="2026-05-29", open_pos=True))  # start dnia 1
    evaluate(c, rt, tick(105_000, balance=105_000, day="2026-05-29", open_pos=True))  # +5000 w dniu 1
    evaluate(c, rt, tick(110_000, balance=110_000, day="2026-05-30", open_pos=True))  # dzień 2: +5000
    # dzień 3: total 12500, best-day=5000 == 40% z 12500 (nie większe) => OK
    r = evaluate(c, rt, tick(112_500, balance=112_500, day="2026-05-31", open_pos=True))
    assert r.metrics["consistency_ok"] is True


def test_config_from_account():
    import types
    acc = types.SimpleNamespace(
        phase="eval_1", initial_balance=50_000, profit_target_p1=10, profit_target_p2=5,
        max_daily_loss_pct=4, max_overall_loss_pct=6, min_trading_days=3,
        drawdown_type="trailing", consistency_pct=40,
    )
    from app.rules import config_from_account, DrawdownType
    c = config_from_account(acc)
    assert c.profit_target_pct == 10 and c.drawdown_type == DrawdownType.TRAILING
    acc.phase = "funded"
    assert config_from_account(acc).profit_target_pct == 0   # funded: brak celu zysku


# ---------------- limit lacznego wolumenu (anty-fullport) ----------------
def _cfg_lots(max_lots: float) -> ChallengeConfig:
    return ChallengeConfig(initial_balance=100_000, profit_target_pct=8,
                           max_daily_loss_pct=5, max_overall_loss_pct=10,
                           max_lots=max_lots)


def test_przekroczenie_limitu_lotow_lamie_regule():
    cfg = _cfg_lots(6.0)
    rt = AccountRuntime(day_start_equity=100_000, day_start_balance=100_000,
                        peak_equity=100_000, day_key="2026-07-27")
    tick = EquityTick(equity=100_000, balance=100_000, open_pnl=0, day_key="2026-07-27",
                      has_open_position=True, volume_lots=6.5, volume_known=True)
    res = evaluate(cfg, rt, tick)
    assert res.failed
    assert any(b[0] == BreachType.MAX_LOTS for b in res.breaches)
    assert "6.50" in res.breaches[0][1] and "6.00" in res.breaches[0][1]


def test_wolumen_w_limicie_nie_lamie_reguly():
    cfg = _cfg_lots(6.0)
    rt = AccountRuntime(day_start_equity=100_000, day_start_balance=100_000,
                        peak_equity=100_000, day_key="2026-07-27")
    tick = EquityTick(equity=100_000, balance=100_000, open_pnl=0, day_key="2026-07-27",
                      has_open_position=True, volume_lots=6.0, volume_known=True)
    assert not evaluate(cfg, rt, tick).failed


def test_niepewny_odczyt_wolumenu_nie_karze_tradera():
    """Gdy nie umiemy odczytac wolumenu, to NASZ problem — konto nie leci."""
    cfg = _cfg_lots(6.0)
    rt = AccountRuntime(day_start_equity=100_000, day_start_balance=100_000,
                        peak_equity=100_000, day_key="2026-07-27")
    tick = EquityTick(equity=100_000, balance=100_000, open_pnl=0, day_key="2026-07-27",
                      has_open_position=True, volume_lots=99.0, volume_known=False)
    assert not evaluate(cfg, rt, tick).failed


def test_limit_lotow_skaluje_sie_z_rozmiarem_konta():
    from app.catalog import max_lots_for
    assert max_lots_for(100_000) == 6.0
    assert max_lots_for(200_000) == 12.0
    assert max_lots_for(50_000) == 3.0
    assert max_lots_for(25_000) == 1.5
    assert max_lots_for(10_000) == 1.0      # podloga: ponizej nie da sie handlowac
