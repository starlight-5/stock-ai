# -*- coding: utf-8 -*-
"""
한국투자증권(KIS) API 주문 관련 모듈입니다.
"""
import json
import logging
import requests
from typing import Dict, Any
from .base import KISBaseClient

logger = logging.getLogger(__name__)

class KISOrderHandler(KISBaseClient):
    """
    주문(매수/매도, 정정/취소, 예약주문)을 담당하는 핸들러 클래스입니다.
    단일 책임 원칙(SRP)에 따라 KIS API 주문 관련 로직만을 담당합니다.
    """
    
    def order_overseas_stock(self, cano: str, acnt_prdt_cd: str, ovrs_excg_cd: str, pdno: str, 
                             ord_qty: str, ovrs_ord_unpr: str, ord_dv: str, ord_dvsn: str = "00") -> Dict[str, Any]:
        """
        [해외주식] 주문 (매수/매도)
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            ovrs_excg_cd (str): 해외거래소코드 (예: NASD, NYSE, AMEX)
            pdno (str): 상품번호/종목코드 (예: AAPL)
            ord_qty (str): 주문수량
            ovrs_ord_unpr (str): 주문단가 (시장가일 경우 "0" 입력)
            ord_dv (str): 주문구분 ("buy": 매수, "sell": 매도)
            ord_dvsn (str): 주문조건 (기본 "00" 지정가)
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/order"
        url = f"{self.base_url}{api_url}"
        
        # TR ID 설정 (미국 기준)
        if ord_dv == "buy":
            tr_id = "TTTT1002U" if self.env_dv == "real" else "VTTT1002U"
            sll_type = ""
        elif ord_dv == "sell":
            tr_id = "TTTT1006U" if self.env_dv == "real" else "VTTT1006U"
            sll_type = "00"
        else:
            logger.error("ord_dv는 'buy' 또는 'sell'이어야 합니다.")
            return {}

        headers = self._get_headers(tr_id)
        
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "PDNO": pdno,
            "ORD_QTY": str(ord_qty),
            "OVRS_ORD_UNPR": str(ovrs_ord_unpr),
            "SLL_TYPE": sll_type,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": ord_dvsn,
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": ""
        }

        try:
            response = requests.post(url, data=json.dumps(body), headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 주문 중 예외 발생: {str(e)}")
            return {}

    def order_overseas_rvsecncl(self, cano: str, acnt_prdt_cd: str, ovrs_excg_cd: str, pdno: str,
                                orgn_odno: str, rvse_cncl_dvsn_cd: str, ord_qty: str, ovrs_ord_unpr: str) -> Dict[str, Any]:
        """
        [해외주식] 정정취소주문
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            ovrs_excg_cd (str): 해외거래소코드
            pdno (str): 상품번호/종목코드
            orgn_odno (str): 원주문번호 (취소/정정 대상)
            rvse_cncl_dvsn_cd (str): 정정취소구분코드 ("01": 정정, "02": 취소)
            ord_qty (str): 주문수량
            ovrs_ord_unpr (str): 주문단가 (취소 시 "0")
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTT1004U" if self.env_dv == "real" else "VTTT1004U"
        headers = self._get_headers(tr_id)
        
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "PDNO": pdno,
            "ORGN_ODNO": orgn_odno,
            "RVSE_CNCL_DVSN_CD": rvse_cncl_dvsn_cd,
            "ORD_QTY": str(ord_qty),
            "OVRS_ORD_UNPR": str(ovrs_ord_unpr),
            "ORD_SVR_DVSN_CD": "0",
            "MGCO_APTM_ODNO": ""
        }

        try:
            response = requests.post(url, data=json.dumps(body), headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 정정취소 중 예외 발생: {str(e)}")
            return {}

    def order_overseas_resv(self, cano: str, acnt_prdt_cd: str, ovrs_excg_cd: str, pdno: str,
                            ord_qty: str, ovrs_ord_unpr: str, ord_dv: str) -> Dict[str, Any]:
        """
        [해외주식] 예약주문접수
        미국거래소 운영시간 외 주식을 예약 매매합니다.
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            ovrs_excg_cd (str): 해외거래소코드
            pdno (str): 상품번호/종목코드
            ord_qty (str): FT주문수량
            ovrs_ord_unpr (str): FT주문단가3
            ord_dv (str): 매도매수구분 ("usBuy": 미국매수, "usSell": 미국매도)
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/order-resv"
        url = f"{self.base_url}{api_url}"
        
        if ord_dv == "usBuy":
            tr_id = "TTTT3014U" if self.env_dv == "real" else "VTTT3014U"
        elif ord_dv == "usSell":
            tr_id = "TTTT3016U" if self.env_dv == "real" else "VTTT3016U"
        else:
            logger.error("예약주문 ord_dv는 'usBuy' 또는 'usSell'이어야 합니다.")
            return {}

        headers = self._get_headers(tr_id)
        
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": pdno,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "FT_ORD_QTY": str(ord_qty),
            "FT_ORD_UNPR3": str(ovrs_ord_unpr)
        }

        try:
            response = requests.post(url, data=json.dumps(body), headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 예약주문접수 중 예외 발생: {str(e)}")
            return {}

    def order_overseas_resv_ccnl(self, cano: str, acnt_prdt_cd: str, rsvn_ord_rcit_dt: str, ovrs_rsvn_odno: str) -> Dict[str, Any]:
        """
        [해외주식] 예약주문접수취소
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            rsvn_ord_rcit_dt (str): 해외주문접수일자
            ovrs_rsvn_odno (str): 해외예약주문번호 (예약주문접수 API 응답의 ODNO)
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/overseas-stock/v1/trading/order-resv-ccnl"
        url = f"{self.base_url}{api_url}"
        
        tr_id = "TTTT3017U" if self.env_dv == "real" else "VTTT3017U"
        headers = self._get_headers(tr_id)
        
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "RSVN_ORD_RCIT_DT": rsvn_ord_rcit_dt,
            "OVRS_RSVN_ODNO": ovrs_rsvn_odno
        }

        try:
            response = requests.post(url, data=json.dumps(body), headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"해외주식 예약주문접수취소 중 예외 발생: {str(e)}")
            return {}
    def order_domestic_stock(self, cano: str, acnt_prdt_cd: str, pdno: str, 
                             ord_qty: str, ord_unpr: str, ord_dv: str, ord_dvsn: str = "01") -> Dict[str, Any]:
        """
        [국내주식] 주문 (현금 매수/매도)
        
        Args:
            cano (str): 종합계좌번호 (8자리)
            acnt_prdt_cd (str): 계좌상품코드 (2자리)
            pdno (str): 종목코드 (6자리)
            ord_qty (str): 주문수량
            ord_unpr (str): 주문단가 (시장가일 경우 "0")
            ord_dv (str): 주문구분 ("buy": 매수, "sell": 매도)
            ord_dvsn (str): 주문방법 ("01": 보통, "02": 시장가 등)
            
        Returns:
            Dict[str, Any]: API 응답 데이터
        """
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"
        url = f"{self.base_url}{api_url}"
        
        # TR ID 설정
        if ord_dv == "buy":
            tr_id = "TTTC0802U" if self.env_dv == "real" else "VTTC0802U"
        elif ord_dv == "sell":
            tr_id = "TTTC0801U" if self.env_dv == "real" else "VTTC0801U"
        else:
            logger.error("ord_dv는 'buy' 또는 'sell'이어야 합니다.")
            return {}

        headers = self._get_headers(tr_id)
        
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": pdno,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(ord_qty),
            "ORD_UNPR": str(ord_unpr)
        }

        try:
            response = requests.post(url, data=json.dumps(body), headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"국내주식 주문 중 예외 발생: {str(e)}")
            return {}
