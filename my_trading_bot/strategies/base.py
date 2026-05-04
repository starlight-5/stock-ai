# -*- coding: utf-8 -*-
"""
모든 자동매매 전략이 반드시 구현해야 할 인터페이스(추상 클래스)를 정의합니다.

OCP(개방-폐쇄 원칙): 새로운 전략(v2, v3...)을 추가할 때
이 클래스를 상속받아 구현하면 기존 코드를 수정할 필요가 없습니다.

ISP(인터페이스 분리 원칙): 전략마다 꼭 필요한 메서드만 강제하여
불필요한 의존성을 제거합니다.
"""

from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """
    자동매매 전략의 추상 기반 클래스입니다.
    모든 전략은 이 클래스를 상속받아 아래 메서드들을 반드시 구현해야 합니다.
    """

    @abstractmethod
    async def run(self) -> None:
        """
        전략의 메인 비동기 루프를 실행합니다.
        봇이 실행되는 동안 이 메서드는 계속 동작합니다.
        """
        raise NotImplementedError

    @abstractmethod
    async def shutdown(self) -> None:
        """
        봇을 안전하게 종료합니다.
        열린 포지션이 있다면 청산하고, 리소스를 정리합니다.
        """
        raise NotImplementedError

    @abstractmethod
    def get_state(self) -> str:
        """
        현재 봇의 상태를 문자열로 반환합니다.
        모니터링, 로그 기록 등에 활용합니다.
        """
        raise NotImplementedError