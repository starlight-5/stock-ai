# -*- coding: utf-8 -*-
"""
봇의 현재 상태(State)와 포지션 정보를 정의하는 모듈입니다.

상태 머신(State Machine) 패턴을 사용하여 봇의 전환 흐름을 명확하게 관리합니다.
상태 전환 흐름:
  IDLE
    └─▶ MONITORING    (장 시작 후 POI 설정 완료)
          └─▶ STANDBY (실시간 가격이 POI 구역에 진입)
                └─▶ IN_POSITION (5분봉 확정 신호로 진입 주문 체결)
                      └─▶ COOLDOWN (포지션 청산 완료 후 손실 재확인)
                            └─▶ MONITORING (안전 확인 완료, 다음 사이클 시작)
  
  모든 상태에서 ──▶ SHUTDOWN (누적 손실 -5% 초과 시 킬 스위치 발동)
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class BotState(Enum):
    """봇의 현재 운영 상태를 나타내는 열거형(Enum) 클래스입니다."""
    
    # 초기 상태: 봇이 시작되기 전 또는 초기화 중인 상태
    IDLE = "IDLE"
    
    # 1단계 - 감시 중: 5분봉 POI 구역을 설정하고, 실시간 가격이 진입하길 기다리는 상태
    MONITORING = "MONITORING"
    
    # 2단계 - 진입 대기: 실시간 가격이 POI 구역에 도달하여 1분봉 확정 신호를 기다리는 상태
    STANDBY = "STANDBY"
    
    # 4단계 - 포지션 보유 중: 매수 주문이 체결되어 실제로 주식을 들고 있는 상태
    IN_POSITION = "IN_POSITION"
    
    # 청산 완료 후 검증 중: 사이클 종료 후 누적 손실 재확인 중인 상태
    COOLDOWN = "COOLDOWN"
    
    # 킬 스위치 발동: 누적 손실 한도 초과로 모든 신규 매매가 정지된 상태
    SHUTDOWN = "SHUTDOWN"


@dataclass
class PositionInfo:
    """
    현재 보유 중인 포지션(Position)의 상세 정보를 담는 데이터 클래스입니다.
    진입 후 2.5단계에서 계산된 값들이 여기에 저장됩니다.
    """
    # 종목 코드 (예: "AAPL")
    symbol: str = ""
    
    # 거래소 코드 (예: "NAS" = 나스닥)
    excd: str = ""
    
    # 진입 평균 단가
    entry_price: float = 0.0
    
    # 총 보유 수량
    total_qty: int = 0
    
    # 현재 남은 수량 (1차 청산 후 절반으로 줄어듦)
    remaining_qty: int = 0
    
    # 손절 라인: 현재 적용 중인 실시간 손절가
    sl_price: float = 0.0

    # 초기 손절가: 트레일링 스탑 계산을 위한 진입 시점의 SL
    initial_sl_price: float = 0.0
    
    # 1차 익절 가격 (RR 3.0 이상)
    tp1_price: float = 0.0
    
    # 2차 익절 가격 (RR 4.0 이상 전량 익절 목표)
    tp2_price: float = 0.0
    
    # 1차 익절(또는 트레일링 스탑) 달성 여부
    tp1_hit: bool = False
    
    # 주문 ID (취소/정정 시 사용)
    order_no: str = ""


@dataclass
class DailyStats:
    """
    하루 동안의 누적 손익 및 킬 스위치 감시를 위한 통계 데이터 클래스입니다.
    매일 장 시작 시 초기화됩니다.
    """
    # 장 시작 시점의 계좌 잔고 스냅샷 (기준값)
    starting_balance: float = 0.0
    
    # 현재 계좌 잔고 (실시간 업데이트)
    current_balance: float = 0.0
    
    # 오늘 완료된 매매 횟수
    trade_count: int = 0
    
    # 오늘 실현된 총 손익 (단위: 달러)
    realized_pnl: float = 0.0
    
    @property
    def drawdown_ratio(self) -> float:
        """시작 잔고 대비 현재 누적 손실 비율을 반환합니다. (손실이면 음수)"""
        if self.starting_balance == 0:
            return 0.0
        return (self.current_balance - self.starting_balance) / self.starting_balance
