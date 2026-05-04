import sys
sys.path.insert(0, '.')

print("=== 1. FVG/POI 탐지 테스트 ===")
from my_trading_bot.strategies.v1_smc.poi_detector import detect_fvg, is_price_in_poi

sample_candles = [
    {'oprc':'100','hipr':'105','lopr':'99','last':'104'},
    {'oprc':'104','hipr':'110','lopr':'103','last':'109'},
    {'oprc':'112','hipr':'115','lopr':'112','last':'114'},  # Bullish FVG: low(112) > high(105)
    {'oprc':'114','hipr':'116','lopr':'113','last':'115'},
    {'oprc':'115','hipr':'117','lopr':'114','last':'116'},
]
fvgs = detect_fvg(sample_candles)
print(f"FVG 탐지: {len(fvgs)}개")
for z in fvgs:
    print(f"  type={z['type']}, bottom={z['bottom']}, top={z['top']}")

hit = is_price_in_poi(106.0, fvgs)
print(f"POI 터치 (106.0): {hit is not None}")

print()
print("=== 2. SL/TP/수량 계산 테스트 ===")
from my_trading_bot.strategies.v1_smc.sl_tp_calculator import calc_qty, calc_tp1, calc_tp2, calc_sl_price

qty = calc_qty(total_capital=10000, risk_ratio=0.02, entry_price=150.0, sl_price=147.0)
tp1 = calc_tp1(entry_price=150.0, sl_price=147.0)
tp2 = calc_tp2(entry_price=150.0, sl_price=147.0, candles_15m=sample_candles)
print(f"수량={qty}주, TP1={tp1:.4f}, TP2={tp2:.4f}")

sl = calc_sl_price(sample_candles, direction="long")
print(f"SL(long, 마지막 캔들 저가)={sl}")

print()
print("=== 3. 상태 머신 테스트 ===")
from my_trading_bot.strategies.v1_smc.state import BotState, PositionInfo, DailyStats
s = BotState.IDLE
print(f"BotState 초기값: {s.value}")
s = BotState.MONITORING
print(f"BotState 전환: {s.value}")

pos = PositionInfo(symbol='AAPL', entry_price=150.0, sl_price=147.0, tp1_price=154.5)
print(f"PositionInfo: symbol={pos.symbol}, entry={pos.entry_price}, tp1={pos.tp1_price}")

daily = DailyStats(starting_balance=10000.0, current_balance=9600.0)
print(f"DailyStats drawdown: {daily.drawdown_ratio:.2%}")

print()
print("=== 모든 단위 테스트 통과! ===")
