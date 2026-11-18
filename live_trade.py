# -*- coding: utf-8 -*-
"""
live_trade.py - runs the smc_core strategy live on MetaTrader 5 (XAUUSD, NY session).

    python live_trade.py --dry-run            # watch and log, send nothing
    python live_trade.py                      # trade the account MT5 is logged into
    python live_trade.py --risk 0.25 --timeframe M15

What it does, once per poll:
  1. pull the last closed candles from MT5 (never the forming one),
  2. manage an open position: at +1R move the SL to entry + cost ("tax"),
  3. on a brand new closed candle, feed it to smc_core.SetupTracker - ALWAYS,
     like backtest.py does, even with a position open or outside the session -
     and only then decide: flat, inside the NY session and under the daily cap
     -> send a market order with SL and the Config.rr take profit.

Safety: XAUUSD only, New York session only, one position at a time,
max_trades_per_day enforced from the broker's own deal history (so a restart
cannot double up), volume rounded DOWN, and --dry-run sends nothing at all.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

import smc_core as core

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

LOG = logging.getLogger("live")

# --------------------------------------------------------------------------- #
# YOUR ACCOUNT - two ways, pick either one
#
#   1. Fill it in right here. The bot logs the terminal into this account
#      itself, so it trades that account whatever the terminal had open.
#
#   2. Leave it as it is (all None) and just log the MetaTrader 5 terminal into
#      your account yourself. The bot attaches to whatever is open there.
#
# Either way it trades that account, demo or real. Order of precedence:
# the command line (--login/--password/--server) beats the environment
# (MT5_LOGIN / MT5_PASSWORD / MT5_SERVER / MT5_PATH), which beats this block.
# Note the password is kept in plain text in this file - the environment
# variable is there if you would rather it were not.
# --------------------------------------------------------------------------- #
ACCOUNT = {
    "login": None,       # 12345678
    "password": None,    # "your-password"
    "server": None,      # "YourBroker-Server"
    "terminal": None,    # r"C:\Program Files\MetaTrader 5\terminal64.exe"
}

TIMEFRAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
}
BAR_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
               "H1": 3600, "H4": 14400}


# --------------------------------------------------------------------------- #
# MT5 plumbing
# --------------------------------------------------------------------------- #
def account_settings(args):
    """Where to log in: the command line, then the environment, then ACCOUNT."""
    login = args.login or os.environ.get("MT5_LOGIN") or ACCOUNT["login"]
    password = args.password or ACCOUNT["password"]   # args default = MT5_PASSWORD
    server = args.server or os.environ.get("MT5_SERVER") or ACCOUNT["server"]
    terminal = args.terminal or os.environ.get("MT5_PATH") or ACCOUNT["terminal"]
    return (int(login) if login else None), password, server, terminal


def connect(login: Optional[int], password: Optional[str], server: Optional[str],
            path: Optional[str]) -> None:
    if mt5 is None:
        raise SystemExit("MetaTrader5 package is missing:  pip install MetaTrader5")
    kwargs = {}
    if path:
        kwargs["path"] = path
    if login:
        LOG.info("logging in to #%s on %s", login, server or "(server from the "
                 "terminal)")
        kwargs.update(login=int(login), password=password or "", server=server or "")
    else:
        LOG.info("no account filled in - attaching to whatever the terminal "
                 "has open")
    if not mt5.initialize(**kwargs):
        raise SystemExit(
            "mt5.initialize failed: %s\nCheck the login / password / server "
            "spelling (the server name must match the terminal exactly), and "
            "that MetaTrader 5 is installed." % (mt5.last_error(),))
    acc = mt5.account_info()
    if acc is None:
        raise SystemExit("connected to the terminal but no account is logged in")
    LOG.info("connected: %s #%s %s | balance %.2f %s | %s account", acc.server,
             acc.login, acc.name, acc.balance, acc.currency, account_kind(acc))


def account_kind(acc) -> str:
    return {mt5.ACCOUNT_TRADE_MODE_DEMO: "DEMO",
            mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST",
            mt5.ACCOUNT_TRADE_MODE_REAL: "REAL"}.get(acc.trade_mode, "?")


def server_utc_offset(symbol: str, fallback: float = 0.0) -> float:
    """Broker server clock minus UTC, in hours (MT5 candle times are SERVER time).

    Estimated from the last tick. While the market is closed that tick is stale,
    which would give a nonsense offset - an implausible answer is dropped.
    Pass --server-offset when you want certainty (most brokers are UTC+2/+3).
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not tick.time:
        return fallback
    hours = round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)
    if -12 <= hours <= 14:
        return float(hours)
    LOG.warning("stale quote for %s (market closed?): offset guess %+g h dropped, "
                "using UTC%+g - pass --server-offset if that is wrong",
                symbol, hours, fallback)
    return float(fallback)


def get_bars(symbol: str, timeframe: str, count: int, utc_offset: float) -> core.Bars:
    """The last `count` CLOSED candles, timestamps converted to real UTC."""
    tf = getattr(mt5, TIMEFRAMES[timeframe])
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count + 1)
    if rates is None or len(rates) < 20:
        raise RuntimeError("no rates for %s %s: %s" % (symbol, timeframe,
                                                       mt5.last_error()))
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - \
        pd.Timedelta(hours=utc_offset)
    df = df.set_index("time")[["open", "high", "low", "close"]]
    return core.Bars.from_dataframe(df.iloc[:-1])     # drop the forming candle


def our_position(symbol: str, magic: int):
    positions = mt5.positions_get(symbol=symbol) or ()
    for p in positions:
        if p.magic == magic:
            return p
    return None


def trades_today(symbol: str, magic: int, cfg: core.Config) -> int:
    """How many of our positions were OPENED today (survives a restart)."""
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=1), now + timedelta(hours=1))
    if not deals:
        return 0
    tz = core.session_timezone(cfg)
    today = pd.Timestamp(now).tz_convert(tz).date()
    n = 0
    for d in deals:
        if d.magic != magic or d.symbol != symbol:
            continue
        if d.entry != mt5.DEAL_ENTRY_IN:
            continue
        if pd.Timestamp(d.time, unit="s", tz="UTC").tz_convert(tz).date() == today:
            n += 1
    return n


def send_order(symbol: str, trade: core.Trade, volume: float, magic: int,
               deviation: int) -> Optional[int]:
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        LOG.error("no quote for %s", symbol)
        return None

    is_buy = trade.direction > 0
    digits = info.digits
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if is_buy else tick.bid,
        "sl": round(trade.sl, digits),
        "tp": round(trade.tp, digits),
        "deviation": int(deviation),
        "magic": int(magic),
        "comment": "smc",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK,
                    mt5.ORDER_FILLING_RETURN):
        request["type_filling"] = filling
        res = mt5.order_send(request)
        if res is None:
            LOG.error("order_send returned None: %s", mt5.last_error())
            return None
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            LOG.info("FILLED %s %.2f lots @ %.2f  SL %.2f  TP %.2f (ticket %s)",
                     "BUY" if is_buy else "SELL", volume, res.price,
                     request["sl"], request["tp"], res.order)
            return res.order
        if res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            LOG.error("order rejected: retcode=%s %s", res.retcode, res.comment)
            return None
    return None


def move_stop(position, new_sl: float, digits: int) -> bool:
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": position.symbol,
        "position": position.ticket,
        "sl": round(new_sl, digits),
        "tp": position.tp,
    })
    ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
    if not ok:
        LOG.error("SL move failed: %s", getattr(res, "comment", mt5.last_error()))
    return ok


def close_position(position, deviation: int) -> bool:
    tick = mt5.symbol_info_tick(position.symbol)
    is_buy = position.type == mt5.POSITION_TYPE_BUY
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "position": position.ticket,
        "price": tick.bid if is_buy else tick.ask,
        "deviation": int(deviation),
        "magic": position.magic,
        "comment": "smc session close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })
    return res is not None and res.retcode == mt5.TRADE_RETCODE_DONE


# --------------------------------------------------------------------------- #
# forward-test journal: one row per closed trade, same columns as the backtest
# --------------------------------------------------------------------------- #
class Journal:
    COLUMNS = ("entry_time", "exit_time", "side", "entry", "sl", "tp", "exit",
               "reason", "r", "pnl", "volume", "be_done", "lq_level",
               "impulse_bars", "pullback_bars", "ob_time", "ticket")

    def __init__(self, path: Optional[str]):
        self.path = path
        self.live = {}                     # ticket -> what we need at exit time
        if not path:
            return
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(self.COLUMNS)

    def opened(self, ticket: int, signal: core.Signal, trade: core.Trade,
               volume: float) -> None:
        if not self.path:
            return
        self.live[int(ticket)] = {"signal": signal, "trade": trade,
                                  "volume": volume, "be": False}

    def moved_to_be(self, ticket: int) -> None:
        if int(ticket) in self.live:
            self.live[int(ticket)]["be"] = True

    def closed(self, ticket: int, cfg: core.Config) -> None:
        """Position gone -> read its deals back and write the row."""
        info = self.live.pop(int(ticket), None)
        if info is None or not self.path:
            return
        deals = mt5.history_deals_get(position=int(ticket)) or ()
        outs = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT,
                                                mt5.DEAL_ENTRY_OUT_BY)]
        if not outs:
            LOG.warning("no closing deal found for %s yet", ticket)
            self.live[int(ticket)] = info          # try again next loop
            return

        trade, signal = info["trade"], info["signal"]
        exit_price = sum(d.price * d.volume for d in outs) / sum(d.volume for d in outs)
        pnl = sum(d.profit + d.commission + d.swap for d in deals)
        tol = max(cfg.pips(1.0), 0.05)
        if abs(exit_price - trade.tp) <= tol:
            reason = "tp"
        elif info["be"] and abs(exit_price - trade.be_price) <= tol:
            reason = "breakeven"
        elif abs(exit_price - trade.initial_sl) <= tol:
            reason = "sl"
        else:
            reason = "closed"

        row = (signal.time, pd.Timestamp(outs[-1].time, unit="s", tz="UTC"),
               "BUY" if trade.direction > 0 else "SELL", round(trade.entry, 2),
               round(trade.initial_sl, 2), round(trade.tp, 2),
               round(exit_price, 2), reason,
               round(trade.r_multiple(exit_price), 3), round(pnl, 2),
               info["volume"], info["be"], round(signal.lq.level, 2),
               signal.impulse_bars, signal.pullback_bars,
               signal.ob_time, int(ticket))
        with open(self.path, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(row)

        n, total = self.totals()
        LOG.info("CLOSED %s at %.2f (%s) %+.2f R, %+.2f -> forward record: "
                 "%d trades, %+.2f R total, %+.3f R average (backtest: +0.315)",
                 row[2], exit_price, reason, row[8], pnl, n, total,
                 total / n if n else 0.0)

    def _rows(self):
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def totals(self):
        """(trades, total R) so far, read back from the file."""
        rows = self._rows()
        return len(rows), sum(float(r["r"]) for r in rows)

    def traded_obs(self):
        """Order blocks already traded - one entry per OB, across restarts."""
        return {r["ob_time"] for r in self._rows() if r.get("ob_time")}


# --------------------------------------------------------------------------- #
# position management: break-even at +1R (rule 6)
# --------------------------------------------------------------------------- #
def manage(position, cfg: core.Config, dry: bool,
           journal: Optional["Journal"] = None) -> None:
    """Rebuilds the trade from the position itself, so a restart loses nothing.

    TP was placed at entry + rr*R, so R = |TP - entry| / rr - no state file.
    """
    d = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    entry = position.price_open
    if not position.tp:
        return
    risk = abs(position.tp - entry) / cfg.rr
    if risk <= 0:
        return

    moved_already = (position.sl >= entry - 1e-9 if d > 0
                     else (position.sl <= entry + 1e-9 and position.sl > 0))
    if moved_already:
        return

    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        return
    price = tick.bid if d > 0 else tick.ask

    trade = core.Trade(direction=d, entry=entry, sl=position.sl, tp=position.tp,
                       risk=risk, initial_sl=position.sl,
                       be_trigger=entry + d * cfg.be_trigger_r * risk,
                       be_price=entry + d * cfg.pips(cfg.be_cost_pips))
    if core.apply_breakeven(trade, high=price, low=price):
        LOG.info("+%.1fR reached (%.2f) -> SL to break-even %.2f",
                 cfg.be_trigger_r, price, trade.sl)
        if dry:
            LOG.info("[dry-run] would move SL to %.2f", trade.sl)
        elif move_stop(position, trade.sl,
                       mt5.symbol_info(position.symbol).digits) and journal:
            journal.moved_to_be(position.ticket)


# --------------------------------------------------------------------------- #
# feeding the tracker - the same way backtest.py does
# --------------------------------------------------------------------------- #
def feed_tracker(tracker: core.SetupTracker, history: core.Bars, need: int,
                 last_fed: Optional[pd.Timestamp]):
    """Hand the tracker every closed candle after `last_fed`, oldest first.

    backtest.py calls tracker.update() on EVERY candle with a window of exactly
    `need` candles ending at that candle. The live bot has to do the same, or the
    tracker's memory (which touches it has seen, which order blocks are spent)
    drifts away from the backtest. So this is called on every new candle -
    whether or not a position is open, inside or outside the session, under or
    over the daily cap - and the trading decision is made afterwards.

    `history` may hold more than `need` candles: after a restart (last_fed is
    None) or a gap (the bot was stalled for a few candles) the missed candles are
    replayed one by one, so the tracker ends up in the state the backtest would
    have. Signals from replayed candles are in the past and are only reported,
    never traded; the setup is spent either way, exactly as in the backtest.

    Returns (signal on the newest candle or None, candles fed, stale signals).
    """
    n = len(history)
    if last_fed is None or last_fed < history.time[min(need, n) - 1]:
        # (re)start, or a gap longer than the history we hold: setups that began
        # before the replay may already be spent, so they are ignored rather than
        # revived. The replay is `need` candles, longer than any setup lives
        # (ob_valid_bars), so every setup that can still trade is rebuilt exactly.
        first = min(need, n)
        tracker.prime(history.window(first - need, first))
    else:
        first = max(need - 1, int(history.time.searchsorted(last_fed, side="right")))
    signal, stale = None, []
    for i in range(first, n):
        found = tracker.update(history.window(i - need + 1, i + 1))
        if found is None:
            continue
        if i == n - 1:
            signal = found
        else:
            stale.append(found)
    return signal, max(0, n - first), stale


# --------------------------------------------------------------------------- #
# main loop
# --------------------------------------------------------------------------- #
def run(cfg: core.Config, args) -> None:
    info = mt5.symbol_info(cfg.symbol)
    if info is None:
        raise SystemExit("symbol %s not found - check the exact broker name "
                         "(XAUUSD.m, GOLD, ...) and pass --symbol" % cfg.symbol)
    if not info.visible:
        mt5.symbol_select(cfg.symbol, True)
        info = mt5.symbol_info(cfg.symbol)

    cfg.contract_size = info.trade_contract_size or cfg.contract_size
    offset = (args.server_offset if args.server_offset is not None
              else server_utc_offset(cfg.symbol))
    LOG.info("%s %s | contract %.0f | point %.5f | server clock = UTC%+g",
             cfg.symbol, cfg.timeframe, cfg.contract_size, info.point, offset)
    LOG.info("session %s-%s %s | risk %.2f%% | max %d trades/day | %s",
             cfg.session_start, cfg.session_end, cfg.session_tz,
             cfg.risk_percent, cfg.max_trades_per_day,
             "DRY RUN (no orders)" if args.dry_run else "LIVE ORDERS")

    start, end = core.session_window(datetime.now(timezone.utc), cfg)
    if start is not None:
        here = datetime.now().astimezone().tzinfo
        LOG.info("%s New York window: %s - %s UTC  (%s - %s your time)",
                 "current" if core.in_session(datetime.now(timezone.utc), cfg)
                 else "next", start.strftime("%a %Y-%m-%d %H:%M"),
                 end.strftime("%H:%M"),
                 start.tz_convert(here).strftime("%a %H:%M"),
                 end.tz_convert(here).strftime("%a %H:%M"))

    need = max(cfg.required_bars(), 200)
    tracker = core.SetupTracker(cfg)      # watches each setup until the OB is touched
    last_seen = None
    journal = Journal(None if args.dry_run else args.journal)
    traded_obs = journal.traded_obs()      # never take the same OB twice
    if journal.path:
        n, total = journal.totals()
        LOG.info("journal %s (%d trades so far, %+.2f R)", journal.path, n, total)

    while True:
        try:
            position = our_position(cfg.symbol, args.magic)

            if position is not None:
                manage(position, cfg, args.dry_run, journal)
                if cfg.close_at_session_end and not core.in_session(
                        datetime.now(timezone.utc), cfg):
                    LOG.info("New York session closed -> flattening")
                    if not args.dry_run:
                        close_position(position, args.deviation)
            else:
                for ticket in list(journal.live):      # it closed on its own
                    journal.closed(ticket, cfg)

            # 2 x need: the extra half is replayed after a restart or a stall
            history = get_bars(cfg.symbol, cfg.timeframe, 2 * need, offset)
            bars = history.window(len(history) - need, len(history))
            bar_time = pd.Timestamp(bars.time[-1])
            fresh_bar = bar_time != last_seen

            signal = None
            if fresh_bar:
                # EVERY closed candle goes to the tracker, like in the backtest -
                # also while a position is open, outside the session, or after the
                # daily cap. Only the decision to trade is gated below.
                signal, fed, stale = feed_tracker(tracker, history, need, last_seen)
                if last_seen is None:
                    LOG.info("tracker warmed up on %d past candles (%d setup(s) "
                             "waiting)", fed, len(tracker.waiting()))
                elif fed > 1:
                    LOG.warning("caught up on %d candles missed since %s", fed - 1,
                                last_seen.strftime("%H:%M UTC"))
                for old in stale:
                    LOG.info("missed while catching up (not traded): %s %s",
                             old.time.strftime("%Y-%m-%d %H:%M"), old.describe(cfg))
                for when, side, why in tracker.failures[-3:]:
                    LOG.debug("setup failed (%s %s): %s", when, side, why)
            last_seen = bar_time

            if fresh_bar and position is not None:
                d = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
                risk = (abs(position.tp - position.price_open) / cfg.rr
                        if position.tp else 0.0)
                now_price = float(bars.close[-1])
                LOG.info("holding %s %.2f @%.2f | now %.2f (%+.2f R) | SL %.2f "
                         "TP %.2f", "BUY" if d > 0 else "SELL", position.volume,
                         position.price_open, now_price,
                         (now_price - position.price_open) * d / risk if risk else 0.0,
                         position.sl, position.tp)
                if signal is not None:     # the backtest skips it too: one at a time
                    LOG.info("setup while a position is open, skipped (spent): %s",
                             signal.describe(cfg))

            if fresh_bar and position is None:
                if not core.in_session(bar_time, cfg):
                    LOG.debug("%s outside the NY session", bar_time)
                elif trades_today(cfg.symbol, args.magic, cfg) >= (
                        cfg.max_trades_per_day + cfg.extra_trades_if_quality):
                    # the hard ceiling: even a perfect setup cannot go past this
                    LOG.info("daily trade limit reached")
                else:
                    LOG.info("%s close %.2f | %s", bar_time.strftime("%H:%M UTC"),
                             bars.close[-1], tracker.state_line(bars))
                    # a good order block may trade beyond the normal daily cap
                    if signal is not None:
                        taken = trades_today(cfg.symbol, args.magic, cfg)
                        cap = cfg.max_trades_per_day
                        if signal.quality >= cfg.good_quality:
                            cap += cfg.extra_trades_if_quality
                        if taken >= cap:
                            LOG.info("%d trades today already; this setup scores %d/%d, "
                                     "cap for it is %d - skipping", taken, signal.quality,
                                     core.max_quality(cfg), cap)
                            signal = None
                    if signal is not None and str(signal.ob_time) in traded_obs:
                        LOG.info("setup on an order block already traded (%s), "
                                 "skipping", signal.ob_time)
                    elif signal is not None:
                        LOG.info("SIGNAL %s", signal.describe(cfg))
                        if take_signal(signal, cfg, args, info, journal):
                            traded_obs.add(str(signal.ob_time))

            _time.sleep(args.poll)

        except KeyboardInterrupt:
            LOG.info("stopped by user")
            return
        except Exception as exc:                      # keep the bot alive
            LOG.exception("loop error: %s", exc)
            _time.sleep(max(5, args.poll))


def take_signal(signal: core.Signal, cfg: core.Config, args, info,
                journal: Optional[Journal] = None) -> bool:
    """True when the setup was acted on (or would have been, in a dry run)."""
    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        LOG.error("no tick, skipping the signal")
        return False

    spread_pips = cfg.to_pips(tick.ask - tick.bid)
    if spread_pips > args.max_spread_pips:
        LOG.warning("spread %.1f pips > %.1f, skipping", spread_pips,
                    args.max_spread_pips)
        return False

    fill = tick.ask if signal.direction > 0 else tick.bid
    trade = core.build_trade(signal, fill, cfg)
    if trade is None:
        LOG.info("setup skipped: stop distance out of the %.0f-%.0f pip band",
                 cfg.min_sl_pips, cfg.max_sl_pips)
        return False

    # the broker's own minimum distance between price and SL/TP
    min_dist = info.trade_stops_level * info.point
    if min_dist and (abs(fill - trade.sl) < min_dist or abs(trade.tp - fill) < min_dist):
        LOG.warning("SL/TP closer than the broker's stop level (%.2f), skipping",
                    min_dist)
        return False

    equity = mt5.account_info().equity
    volume = core.lot_size(equity, trade.risk, cfg, info.volume_min,
                           info.volume_max, info.volume_step)
    money = trade.risk * volume * cfg.contract_size
    LOG.info("entry %.2f  SL %.2f (%.1f pips)  TP %.2f (%.1fR)  BE at %.2f  "
             "%.2f lots = %.2f risk", fill, trade.sl, cfg.to_pips(trade.risk),
             trade.tp, cfg.rr, trade.be_trigger, volume, money)

    if args.dry_run:
        LOG.info("[dry-run] no order sent")
        return True

    if send_order(cfg.symbol, trade, volume, args.magic, args.deviation) is None:
        return False
    position = our_position(cfg.symbol, args.magic)
    if position is not None and journal is not None:
        # the fill price is what the broker gave us, not what we asked for
        filled = core.build_trade(signal, position.price_open, cfg) or trade
        journal.opened(position.ticket, signal, filled, position.volume)
    return True


# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="SMC live trader (XAUUSD, NY session)")
    p.add_argument("--symbol", default="XAUUSD", help="broker name, e.g. XAUUSD.m")
    p.add_argument("--timeframe", default="M5", choices=sorted(TIMEFRAMES))
    p.add_argument("--risk", type=float, default=0.5, help="%% of equity per trade")
    p.add_argument("--max-trades", type=int, default=3, help="per day")
    p.add_argument("--dry-run", action="store_true", help="log signals, send nothing")
    p.add_argument("--journal", default="reports/forward_trades.csv",
                   help="CSV of closed trades, for comparing against the backtest")
    p.add_argument("--close-at-session-end", action="store_true")
    p.add_argument("--magic", type=int, default=777001)
    p.add_argument("--deviation", type=int, default=20, help="max slippage, points")
    p.add_argument("--max-spread-pips", type=float, default=10.0)
    p.add_argument("--poll", type=float, default=5.0, help="seconds between checks")
    p.add_argument("--server-offset", type=float, default=None,
                   help="broker clock minus UTC in hours (auto-detected if unset)")
    p.add_argument("--login", type=int, default=None)
    p.add_argument("--password", default=os.environ.get("MT5_PASSWORD"))
    p.add_argument("--server", default=None)
    p.add_argument("--terminal", default=None, help="path to terminal64.exe")
    p.add_argument("--log", default="live_trade.log")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(args.log, encoding="utf-8")],
    )

    if "XAU" not in args.symbol.upper():
        raise SystemExit("this bot trades XAUUSD only (got %r)" % args.symbol)

    cfg = core.Config(symbol=args.symbol, timeframe=args.timeframe,
                      risk_percent=args.risk, max_trades_per_day=args.max_trades,
                      close_at_session_end=args.close_at_session_end)

    connect(*account_settings(args))

    terminal = mt5.terminal_info()
    if args.dry_run:
        LOG.info("DRY RUN: watching only, no order will be sent")
    elif terminal is not None and not terminal.trade_allowed:
        LOG.warning("AutoTrading is OFF in the terminal - orders will be "
                    "rejected. Turn on the 'Algo Trading' button and restart.")

    try:
        run(cfg, args)
    finally:
        mt5.shutdown()
        LOG.info("disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
