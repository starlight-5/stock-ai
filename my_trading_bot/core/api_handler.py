# -*- coding: utf-8 -*-
"""
한국투자증권(KIS) API 통신을 담당하는 메인 파사드(Facade) 모듈입니다.
내부적으로 Auth, Order, Account 핸들러를 조합하여 제공합니다.
"""
import os
import logging
from dotenv import load_dotenv, find_dotenv
from typing import Dict, Any

from .kis_api.auth import KISAuthHandler
from .kis_api.order import KISOrderHandler
from .kis_api.account import KISAccountHandler
from .kis_api.market import KISMarketHandler
from .kis_api.analysis import KISAnalysisHandler

# 로깅 설정
logger = logging.getLogger(__name__)
# .env 파일 로드 (상위 폴더에 있는 .env 파일을 자동으로 찾아줍니다)
load_dotenv(find_dotenv())

class KISApiHandler:
    """
    한국투자증권(KIS) 오픈 API 통신을 담당하는 파사드 클래스입니다.
    이 클래스를 통해 모든 KIS API 기능(인증, 주문, 조회)에 접근할 수 있습니다.
    외부 인터페이스는 기존과 동일하게 유지하여 하위 호환성을 보장합니다.
    """
    
    def __init__(self, appkey: str, appsecret: str, env_dv: str = "real"):
        """
        KISApiHandler 인스턴스를 초기화합니다.
        
        Args:
            appkey (str): 발급받은 앱키 (App Key)
            appsecret (str): 발급받은 앱시크릿키 (App Secret)
            env_dv (str): 환경 구분 ('real': 실전 투자, 'demo': 모의 투자)
        """
        self.appkey = appkey
        self.appsecret = appsecret
        self.env_dv = env_dv
        self._access_token = ""
        
        # 각 하위 기능별 핸들러 초기화
        self._auth = KISAuthHandler(appkey, appsecret, env_dv, self._access_token)
        self._order = KISOrderHandler(appkey, appsecret, env_dv, self._access_token)
        self._account = KISAccountHandler(appkey, appsecret, env_dv, self._access_token)
        self._market = KISMarketHandler(appkey, appsecret, env_dv, self._access_token)
        self._analysis = KISAnalysisHandler(appkey, appsecret, env_dv, self._access_token)
        
    @property
    def access_token(self) -> str:
        """현재 설정된 접근 토큰을 반환합니다."""
        return self._access_token
        
    @access_token.setter
    def access_token(self, token: str):
        """
        접근 토큰을 설정하고, 모든 하위 핸들러에 동기화합니다.
        """
        self._access_token = token
        # 하위 핸들러에 토큰 동기화
        self._auth.access_token = token
        self._order.access_token = token
        self._account.access_token = token
        self._market.access_token = token
        self._analysis.access_token = token
        logger.info("access_token이 모든 내부 핸들러에 동기화되었습니다.")
        
    # ==========================================
    # [인증 API 위임]
    # ==========================================
    def issue_access_token(self) -> Dict[str, Any]:
        """[인증] OAuth 접근토큰을 발급받습니다."""
        res = self._auth.issue_access_token()
        if "access_token" in res:
            # 발급받은 토큰을 프로퍼티 셋터를 통해 저장 및 동기화
            self.access_token = res["access_token"]
        return res
        
    def issue_ws_token(self) -> Dict[str, Any]:
        """[인증] WebSocket 실시간 데이터 접속키를 발급받습니다."""
        return self._auth.issue_ws_token()
        
    # ==========================================
    # [주문 API 위임]
    # ==========================================
    def order_overseas_stock(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문 (매수/매도)"""
        return self._order.order_overseas_stock(*args, **kwargs)
        
    def order_overseas_rvsecncl(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 정정취소주문"""
        return self._order.order_overseas_rvsecncl(*args, **kwargs)
        
    def order_overseas_resv(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 예약주문접수"""
        return self._order.order_overseas_resv(*args, **kwargs)
        
    def order_overseas_resv_ccnl(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 예약주문접수취소"""
        return self._order.order_overseas_resv_ccnl(*args, **kwargs)

    # ==========================================
    # [조회/계좌 API 위임]
    # ==========================================
    def inquire_overseas_psamount(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 해외주식 매수가능금액조회"""
        return self._account.inquire_overseas_psamount(*args, **kwargs)

    def inquire_overseas_nccs(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 해외주식 미체결내역"""
        return self._account.inquire_overseas_nccs(*args, **kwargs)

    def inquire_overseas_balance(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 해외주식 잔고"""
        return self._account.inquire_overseas_balance(*args, **kwargs)

    def inquire_overseas_ccnl(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 해외주식 주문체결내역"""
        return self._account.inquire_overseas_ccnl(*args, **kwargs)

    def inquire_overseas_present_balance(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 해외주식 체결기준현재잔고"""
        return self._account.inquire_overseas_present_balance(*args, **kwargs)

    def inquire_overseas_order_resv_list(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문/계좌 > 해외주식 예약주문조회"""
        return self._account.inquire_overseas_order_resv_list(*args, **kwargs)

    def inquire_paymt_stdr_balance(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문/계좌 > 해외주식 결제기준잔고"""
        return self._account.inquire_paymt_stdr_balance(*args, **kwargs)

    def inquire_period_trans(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문/계좌 > 해외주식 일별거래내역"""
        return self._account.inquire_period_trans(*args, **kwargs)

    def inquire_period_profit(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문/계좌 > 해외주식 기간손익"""
        return self._account.inquire_period_profit(*args, **kwargs)

    def inquire_foreign_margin(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문/계좌 > 해외증거금 통화별조회"""
        return self._account.inquire_foreign_margin(*args, **kwargs)

    def inquire_algo_ordno(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문/계좌 > 해외주식 지정가주문번호조회"""
        return self._account.inquire_algo_ordno(*args, **kwargs)

    def inquire_algo_ccnl(self, *args, **kwargs) -> Dict[str, Any]:
        """[해외주식] 주문/계좌 > 해외주식 지정가체결내역조회"""
        return self._account.inquire_algo_ccnl(*args, **kwargs)
    # ==========================================
    # 기본 시세 (Market Data) 기능 위임
    # ==========================================

    def get_price_detail(self, excd: str, symb: str) -> Dict[str, Any]:
        """해외주식 현재가상세"""
        return self._market.get_price_detail(excd, symb)

    def get_asking_price(self, excd: str, symb: str) -> Dict[str, Any]:
        """해외주식 현재가 호가(1호가)"""
        return self._market.get_asking_price(excd, symb)

    def get_price(self, excd: str, symb: str) -> Dict[str, Any]:
        """해외주식 현재체결가"""
        return self._market.get_price(excd, symb)

    def get_quot_ccnl(self, excd: str, symb: str, tday: str = "", keyb: str = "") -> Dict[str, Any]:
        """해외주식 체결추이"""
        return self._market.get_quot_ccnl(excd, symb, tday, keyb)

    def get_time_itemchartprice(self, excd: str, symb: str, nmin: str = "01", pinc: str = "1", 
                                ncnt: str = "30", dtm: str = "", keyb: str = "") -> Dict[str, Any]:
        """해외주식 분봉조회"""
        return self._market.get_time_itemchartprice(excd, symb, nmin, pinc, ncnt, dtm, keyb)

    def get_dailyprice(self, excd: str, symb: str, gubn: str = "0", modp: str = "0", 
                       tday: str = "", keyb: str = "") -> Dict[str, Any]:
        """해외주식 기간별시세"""
        return self._market.get_dailyprice(excd, symb, gubn, modp, tday, keyb)

    def get_daily_chartprice(self, excd: str, symb: str, gubn: str = "0", modp: str = "1", 
                             bymd: str = "", dtm: str = "", keyb: str = "") -> Dict[str, Any]:
        """해외주식 종목 기간별시세"""
        return self._market.get_daily_chartprice(excd, symb, gubn, modp, bymd, dtm, keyb)

    def get_inquire_search(self, excd: str, prcs: str, prce: str, vol: str, 
                           amt: str, rate: str, rate2: str) -> Dict[str, Any]:
        """해외주식 조건검색"""
        return self._market.get_inquire_search(excd, prcs, prce, vol, amt, rate, rate2)

    def get_countries_holiday(self, dt: str, excd: str) -> Dict[str, Any]:
        """해외결제일자조회"""
        return self._market.get_countries_holiday(dt, excd)

    def get_search_info(self, prdt_type: str, prdt_cd: str) -> Dict[str, Any]:
        """해외주식 상품기본정보"""
        return self._market.get_search_info(prdt_type, prdt_cd)

    def get_industry_theme(self, excd: str, iscd: str) -> Dict[str, Any]:
        """해외주식 업종별시세"""
        return self._market.get_industry_theme(excd, iscd)

    def get_industry_price(self, excd: str, gb1: str = "0") -> Dict[str, Any]:
        """해외주식 업종별코드조회"""
        return self._market.get_industry_price(excd, gb1)
        
    # ==========================================
    # 시세 분석 (Market Analysis) 기능 위임
    # ==========================================

    def get_price_fluct(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 가격급등락"""
        return self._analysis.get_price_fluct(excd, **kwargs)

    def get_volume_surge(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 거래량급증"""
        return self._analysis.get_volume_surge(excd, **kwargs)

    def get_volume_power(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 매수체결강도상위"""
        return self._analysis.get_volume_power(excd, **kwargs)

    def get_updown_rate(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 상승율/하락율"""
        return self._analysis.get_updown_rate(excd, **kwargs)

    def get_trade_vol(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 거래량순위"""
        return self._analysis.get_trade_vol(excd, **kwargs)

    def get_trade_pbmn(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 거래대금순위"""
        return self._analysis.get_trade_pbmn(excd, **kwargs)

    def get_trade_growth(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 거래증가율순위"""
        return self._analysis.get_trade_growth(excd, **kwargs)

    def get_trade_turnover(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """해외주식 거래회전율순위"""
        return self._analysis.get_trade_turnover(excd, **kwargs)
