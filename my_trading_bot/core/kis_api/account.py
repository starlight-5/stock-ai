# -*- coding: utf-8 -*-
"""
한국투자증권(KIS) API 계좌 및 조회 관련 모듈입니다.
"""
import json
import logging
import requests
from typing import Dict, Any
from .base import KISBaseClient

logger = logging.getLogger(__name__)

class KISAccountHandler(KISBaseClient):
    """
    잔고, 체결내역, 예수금 등의 조회를 담당하는 핸들러 클래스입니다.
    단일 책임 원칙(SRP)에 따라 KIS API 조회/계좌 관련 로직만을 담당합니다.
    """
    
    def inquire_overseas_psamount(self, cano: str, acnt_prdt_cd: str, ovrs_excg_cd: str, ovrs_ord_unpr: str, item_cd: str) -> Dict[str, Any]:
        """
        [해외주식] 해외주식 매수가능금액조회
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            ovrs_excg_cd (str): 해외거래소코드 (예: NASD, NYSE, AMEX 등)
            ovrs_ord_unpr (str): 해외주문단가
            item_cd (str): 종목코드
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-psamount"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTS3007R" if self.env_dv == "real" else "VTTS3007R"
        headers = self._get_headers(tr_id)
        
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "OVRS_ORD_UNPR": str(ovrs_ord_unpr),
            "ITEM_CD": item_cd
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 매수가능금액조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_overseas_nccs(self, cano: str, acnt_prdt_cd: str, ovrs_excg_cd: str, sort_sqn: str = "DS",
                              ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> Dict[str, Any]:
        """
        [해외주식] 해외주식 미체결내역
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            ovrs_excg_cd (str): 해외거래소코드
            sort_sqn (str): 정렬순서 ("DS": 역순, 그외: 정순)
            ctx_area_fk200 (str): 연속조회검색조건200 (초기 공란)
            ctx_area_nk200 (str): 연속조회키200 (초기 공란)
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-nccs"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTS3018R" if self.env_dv == "real" else "VTTS3018R"
        
        headers = self._get_headers(tr_id)
        if ctx_area_fk200 or ctx_area_nk200:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "SORT_SQN": sort_sqn,
            "CTX_AREA_FK200": ctx_area_fk200,
            "CTX_AREA_NK200": ctx_area_nk200
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 미체결내역조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_overseas_balance(self, cano: str, acnt_prdt_cd: str, ovrs_excg_cd: str, tr_crcy_cd: str = "USD",
                                 ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> Dict[str, Any]:
        """
        [해외주식] 해외주식 잔고
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            ovrs_excg_cd (str): 해외거래소코드 (예: NASD)
            tr_crcy_cd (str): 거래통화코드 (예: USD)
            ctx_area_fk200 (str): 연속조회검색조건200
            ctx_area_nk200 (str): 연속조회키200
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-balance"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTS3012R" if self.env_dv == "real" else "VTTS3012R"
        
        headers = self._get_headers(tr_id)
        if ctx_area_fk200 or ctx_area_nk200:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "TR_CRCY_CD": tr_crcy_cd,
            "CTX_AREA_FK200": ctx_area_fk200,
            "CTX_AREA_NK200": ctx_area_nk200
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 잔고조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_overseas_ccnl(self, cano: str, acnt_prdt_cd: str, pdno: str, ord_strt_dt: str, ord_end_dt: str,
                              sll_buy_dvsn: str = "00", ccld_nccs_dvsn: str = "00", ovrs_excg_cd: str = "%",
                              sort_sqn: str = "DS", ord_dt: str = "", ord_gno_brno: str = "", odno: str = "",
                              ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> Dict[str, Any]:
        """
        [해외주식] 해외주식 주문체결내역
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            pdno (str): 상품번호 ("%" 입력시 전종목)
            ord_strt_dt (str): 주문시작일자 (YYYYMMDD)
            ord_end_dt (str): 주문종료일자 (YYYYMMDD)
            sll_buy_dvsn (str): 매도매수구분 ("00":전체, "01":매도, "02":매수)
            ccld_nccs_dvsn (str): 체결미체결구분 ("00":전체, "01":체결, "02":미체결)
            ovrs_excg_cd (str): 해외거래소코드 ("%" 입력시 전종목)
            sort_sqn (str): 정렬순서 ("DS":역순, "AS":정순)
            ord_dt, ord_gno_brno, odno: 주문일자, 주문지점, 주문번호 (기본 공란)
            ctx_area_fk200, ctx_area_nk200: 연속조회조건
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTS3035R" if self.env_dv == "real" else "VTTS3035R"
        
        headers = self._get_headers(tr_id)
        if ctx_area_fk200 or ctx_area_nk200:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": pdno,
            "ORD_STRT_DT": ord_strt_dt,
            "ORD_END_DT": ord_end_dt,
            "SLL_BUY_DVSN": sll_buy_dvsn,
            "CCLD_NCCS_DVSN": ccld_nccs_dvsn,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "SORT_SQN": sort_sqn,
            "ORD_DT": ord_dt,
            "ORD_GNO_BRNO": ord_gno_brno,
            "ODNO": odno,
            "CTX_AREA_FK200": ctx_area_fk200,
            "CTX_AREA_NK200": ctx_area_nk200
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 주문체결내역조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_overseas_present_balance(self, cano: str, acnt_prdt_cd: str, wcrc_frcr_dvsn_cd: str = "02",
                                         natn_cd: str = "840", tr_mket_cd: str = "00", inqr_dvsn_cd: str = "00") -> Dict[str, Any]:
        """
        [해외주식] 해외주식 체결기준현재잔고
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            wcrc_frcr_dvsn_cd (str): 원화외화구분 ("01": 원화, "02": 외화)
            natn_cd (str): 국가코드 ("840": 미국)
            tr_mket_cd (str): 거래시장코드 ("00": 전체)
            inqr_dvsn_cd (str): 조회구분코드 ("00": 전체)
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "CTRP6504R" if self.env_dv == "real" else "VTRP6504R"
        headers = self._get_headers(tr_id)
        
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "WCRC_FRCR_DVSN_CD": wcrc_frcr_dvsn_cd,
            "NATN_CD": natn_cd,
            "TR_MKET_CD": tr_mket_cd,
            "INQR_DVSN_CD": inqr_dvsn_cd
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 체결기준현재잔고조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_overseas_order_resv_list(self, cano: str, acnt_prdt_cd: str, inqr_strt_dt: str, inqr_end_dt: str,
                                         inqr_dvsn_cd: str, ovrs_excg_cd: str, nat_dv: str = "us", prdt_type_cd: str = "",
                                         ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> Dict[str, Any]:
        """
        [해외주식] 주문/계좌 > 해외주식 예약주문조회
        
        Args:
            cano (str): 종합계좌번호
            acnt_prdt_cd (str): 계좌상품코드 (예: "01")
            inqr_strt_dt (str): 조회시작일자 (YYYYMMDD)
            inqr_end_dt (str): 조회종료일자 (YYYYMMDD)
            inqr_dvsn_cd (str): 조회구분코드 ("00": 전체, "01": 일반, "02": 미니스탁)
            ovrs_excg_cd (str): 해외거래소코드 ("NASD" 등)
            nat_dv (str): 국가구분코드 ("us": 미국, "asia": 아시아)
            prdt_type_cd (str): 상품유형코드
            ctx_area_fk200 (str): 연속조회검색조건200
            ctx_area_nk200 (str): 연속조회키200
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/order-resv-list"
        url = f"{self.base_url}{api_url}"
        
        if nat_dv == "us":
            tr_id = "TTTT3039R" if self.env_dv == "real" else "VTTT3039R"
        elif nat_dv == "asia":
            tr_id = "TTTS3014R" if self.env_dv == "real" else "VTTS3014R"
        else:
            raise ValueError("nat_dv can only be 'us' or 'asia'")
            
        headers = self._get_headers(tr_id)
        if ctx_area_fk200 or ctx_area_nk200:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "INQR_STRT_DT": inqr_strt_dt,
            "INQR_END_DT": inqr_end_dt,
            "INQR_DVSN_CD": inqr_dvsn_cd,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "PRDT_TYPE_CD": prdt_type_cd,
            "CTX_AREA_FK200": ctx_area_fk200,
            "CTX_AREA_NK200": ctx_area_nk200
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 예약주문조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_paymt_stdr_balance(self, cano: str, acnt_prdt_cd: str, bass_dt: str, 
                                   wcrc_frcr_dvsn_cd: str, inqr_dvsn_cd: str) -> Dict[str, Any]:
        """
        [해외주식] 주문/계좌 > 해외주식 결제기준잔고
        
        Args:
            cano (str): 종합계좌번호
            acnt_prdt_cd (str): 계좌상품코드 (예: "01")
            bass_dt (str): 기준일자 (YYYYMMDD)
            wcrc_frcr_dvsn_cd (str): 원화외화구분코드 ("01": 원화기준, "02": 외화기준)
            inqr_dvsn_cd (str): 조회구분코드 ("00": 전체, "01": 일반, "02": 미니스탁)
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "CTRP6010R" if self.env_dv == "real" else "VTRP6010R"
        headers = self._get_headers(tr_id)
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "BASS_DT": bass_dt,
            "WCRC_FRCR_DVSN_CD": wcrc_frcr_dvsn_cd,
            "INQR_DVSN_CD": inqr_dvsn_cd
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 결제기준잔고조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_period_trans(self, cano: str, acnt_prdt_cd: str, erlm_strt_dt: str, erlm_end_dt: str,
                             ovrs_excg_cd: str, sll_buy_dvsn_cd: str, pdno: str = "", loan_dvsn_cd: str = "",
                             ctx_area_fk100: str = "", ctx_area_nk100: str = "") -> Dict[str, Any]:
        """
        [해외주식] 주문/계좌 > 해외주식 일별거래내역
        
        Args:
            cano (str): 종합계좌번호
            acnt_prdt_cd (str): 계좌상품코드 (예: "01")
            erlm_strt_dt (str): 등록시작일자 (YYYYMMDD)
            erlm_end_dt (str): 등록종료일자 (YYYYMMDD)
            ovrs_excg_cd (str): 해외거래소코드
            sll_buy_dvsn_cd (str): 매도매수구분코드 ("00": 전체, "01": 매도, "02": 매수)
            pdno (str): 상품번호
            loan_dvsn_cd (str): 대출구분코드
            ctx_area_fk100 (str): 연속조회검색조건100
            ctx_area_nk100 (str): 연속조회키100
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-period-trans"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "CTOS4001R" if self.env_dv == "real" else "VTOS4001R"
        headers = self._get_headers(tr_id)
        if ctx_area_fk100 or ctx_area_nk100:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "ERLM_STRT_DT": erlm_strt_dt,
            "ERLM_END_DT": erlm_end_dt,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "PDNO": pdno,
            "SLL_BUY_DVSN_CD": sll_buy_dvsn_cd,
            "LOAN_DVSN_CD": loan_dvsn_cd,
            "CTX_AREA_FK100": ctx_area_fk100,
            "CTX_AREA_NK100": ctx_area_nk100
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 일별거래내역조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_period_profit(self, cano: str, acnt_prdt_cd: str, ovrs_excg_cd: str, inqr_strt_dt: str, inqr_end_dt: str,
                              wcrc_frcr_dvsn_cd: str, natn_cd: str = "", crcy_cd: str = "USD", pdno: str = "",
                              ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> Dict[str, Any]:
        """
        [해외주식] 주문/계좌 > 해외주식 기간손익
        
        Args:
            cano (str): 종합계좌번호
            acnt_prdt_cd (str): 계좌상품코드 (예: "01")
            ovrs_excg_cd (str): 해외거래소코드 (공란: 전체, NASD: 미국 등)
            inqr_strt_dt (str): 조회시작일자 (YYYYMMDD)
            inqr_end_dt (str): 조회종료일자 (YYYYMMDD)
            wcrc_frcr_dvsn_cd (str): 원화외화구분코드 ("01": 외화, "02": 원화)
            natn_cd (str): 국가코드 (공란)
            crcy_cd (str): 통화코드 ("USD" 등)
            pdno (str): 상품번호 (공란: 전체)
            ctx_area_fk200 (str): 연속조회검색조건200
            ctx_area_nk200 (str): 연속조회키200
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-period-profit"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTS3039R" if self.env_dv == "real" else "VTTS3039R"
        headers = self._get_headers(tr_id)
        if ctx_area_fk200 or ctx_area_nk200:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "NATN_CD": natn_cd,
            "CRCY_CD": crcy_cd,
            "PDNO": pdno,
            "INQR_STRT_DT": inqr_strt_dt,
            "INQR_END_DT": inqr_end_dt,
            "WCRC_FRCR_DVSN_CD": wcrc_frcr_dvsn_cd,
            "CTX_AREA_FK200": ctx_area_fk200,
            "CTX_AREA_NK200": ctx_area_nk200
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 기간손익조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_foreign_margin(self, cano: str, acnt_prdt_cd: str) -> Dict[str, Any]:
        """
        [해외주식] 주문/계좌 > 해외증거금 통화별조회
        
        Args:
            cano (str): 종합계좌번호
            acnt_prdt_cd (str): 계좌상품코드 (예: "01")
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/foreign-margin"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTC2101R" if self.env_dv == "real" else "VTTC2101R"
        headers = self._get_headers(tr_id)
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외증거금 통화별조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_algo_ordno(self, cano: str, acnt_prdt_cd: str, trad_dt: str,
                           ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> Dict[str, Any]:
        """
        [해외주식] 주문/계좌 > 해외주식 지정가주문번호조회 (TWAP, VWAP 주문)
        
        Args:
            cano (str): 종합계좌번호
            acnt_prdt_cd (str): 계좌상품코드 (예: "01")
            trad_dt (str): 거래일자 (YYYYMMDD)
            ctx_area_fk200 (str): 연속조회검색조건200
            ctx_area_nk200 (str): 연속조회키200
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/algo-ordno"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTS6058R" if self.env_dv == "real" else "VTTS6058R"
        headers = self._get_headers(tr_id)
        if ctx_area_fk200 or ctx_area_nk200:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "TRAD_DT": trad_dt,
            "CTX_AREA_FK200": ctx_area_fk200,
            "CTX_AREA_NK200": ctx_area_nk200
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 지정가주문번호조회 중 예외 발생: {str(e)}")
            return {}

    def inquire_algo_ccnl(self, cano: str, acnt_prdt_cd: str, ord_dt: str = "", ord_gno_brno: str = "", odno: str = "",
                          ttlz_icld_yn: str = "", ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> Dict[str, Any]:
        """
        [해외주식] 주문/계좌 > 해외주식 지정가체결내역조회 (TWAP, VWAP 체결내역)
        
        Args:
            cano (str): 종합계좌번호
            acnt_prdt_cd (str): 계좌상품코드 (예: "01")
            ord_dt (str): 주문일자
            ord_gno_brno (str): 주문채번지점번호
            odno (str): 주문번호
            ttlz_icld_yn (str): 집계포함여부
            ctx_area_fk200 (str): 연속조회조건200
            ctx_area_nk200 (str): 연속조회키200
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-algo-ccnl"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTS6059R" if self.env_dv == "real" else "VTTS6059R"
        headers = self._get_headers(tr_id)
        if ctx_area_fk200 or ctx_area_nk200:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "ORD_DT": ord_dt,
            "ORD_GNO_BRNO": ord_gno_brno,
            "ODNO": odno,
            "TTLZ_ICLD_YN": ttlz_icld_yn,
            "CTX_AREA_NK200": ctx_area_nk200,
            "CTX_AREA_FK200": ctx_area_fk200
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 지정가체결내역조회 중 예외 발생: {str(e)}")
            return {}
    def inquire_domestic_balance(self, cano: str, acnt_prdt_cd: str, afhr_flpr_yn: str = "N", 
                                 inqr_dvsn: str = "02", unpr_dvsn: str = "01", fund_sttl_icld_yn: str = "N",
                                 fncg_amt_auto_rdpt_yn: str = "N", prcs_dvsn: str = "00",
                                 ctx_area_fk100: str = "", ctx_area_nk100: str = "") -> Dict[str, Any]:
        """
        [국내주식] 주식잔고조회
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            afhr_flpr_yn (str): 시간외단일가여부 (N: 기본, Y: 시간외단일가)
            inqr_dvsn (str): 조회구분 (01: 대출일별, 02: 종목별)
            unpr_dvsn (str): 단가구분 (01: 기본)
            fund_sttl_icld_yn (str): 펀드결제분포함여부 (N: 미포함)
            fncg_amt_auto_rdpt_yn (str): 융자금액자동상환여부 (N: 미상환)
            prcs_dvsn (str): 처리구분 (00: 전일매매포함, 01: 전일매매미포함)
            ctx_area_fk100 (str): 연속조회검색조건100
            ctx_area_nk100 (str): 연속조회키100
            
        Returns:
            Dict[str, Any]: API 응답 데이터 (output1: 잔고내역, output2: 계좌상세)
        """
        api_url = "/uapi/domestic-stock/v1/trading/inquire-balance"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTC8434R" if self.env_dv == "real" else "VTTC8434R"
        headers = self._get_headers(tr_id)
        if ctx_area_fk100 or ctx_area_nk100:
            headers["tr_cont"] = "N"
            
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": afhr_flpr_yn,
            "OFL_YN": "",
            "INQR_DVSN": inqr_dvsn,
            "UNPR_DVSN": unpr_dvsn,
            "FUND_STTL_ICLD_YN": fund_sttl_icld_yn,
            "FNCG_AMT_AUTO_RDPT_YN": fncg_amt_auto_rdpt_yn,
            "PRCS_DVSN": prcs_dvsn,
            "CTX_AREA_FK100": ctx_area_fk100,
            "CTX_AREA_NK100": ctx_area_nk100
        }

        try:
            response = requests.get(url, params=params, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"국내주식 잔고조회 중 예외 발생: {str(e)}")
            return {}
