from typing import Dict, Any, Optional
from .base import KISBaseClient

class KISAnalysisHandler(KISBaseClient):
    """
    한국투자증권 API 해외주식 시세 분석 (Market Analysis) 핸들러
    
    지원하는 API 목록:
    1. 가격급등락 (price_fluct)
    2. 거래량급증 (volume_surge)
    3. 매수체결강도상위 (volume_power)
    4. 상승율/하락율 (updown_rate)
    5. 거래량순위 (trade_vol)
    6. 거래대금순위 (trade_pbmn)
    7. 거래증가율순위 (trade_growth)
    8. 거래회전율순위 (trade_turnover)
    """

    def _call_analysis_api(self, tr_id: str, url_path: str, params: Dict[str, Any], tr_cont: str = "") -> Dict[str, Any]:
        """시세 분석 공통 API 호출 헬퍼"""
        headers = self._get_headers(tr_id)
        if tr_cont:
            headers["tr_cont"] = tr_cont
            
        url = f"{self.base_url}{url_path}"
        return self.get_request(url, headers=headers, params=params)
        
    def get_price_fluct(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        1. 해외주식 가격급등락
        (TR_ID: HHDFS76260000)
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
        return self._call_analysis_api("HHDFS76260000", "/uapi/overseas-stock/v1/ranking/price-fluct", params)

    def get_volume_surge(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        2. 해외주식 거래량급증
        (TR_ID: HHDFS76270000)
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
        return self._call_analysis_api("HHDFS76270000", "/uapi/overseas-stock/v1/ranking/volume-surge", params)

    def get_volume_power(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        3. 해외주식 매수체결강도상위
        (TR_ID: HHDFS76280000)
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
        return self._call_analysis_api("HHDFS76280000", "/uapi/overseas-stock/v1/ranking/volume-power", params)

    def get_updown_rate(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        4. 해외주식 상승율/하락율
        (TR_ID: HHDFS76290000)
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
        return self._call_analysis_api("HHDFS76290000", "/uapi/overseas-stock/v1/ranking/updown-rate", params)

    def get_trade_vol(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        5. 해외주식 거래량순위
        (TR_ID: HHDFS76310010)
        nday: 0=당일, vol_rang: 0=전체
        """
        params = {
            "AUTH": "",
            "EXCD": excd,
            "NDAY": kwargs.get("nday", "0"),       # 당일
            "VOL_RANG": kwargs.get("vol_rang", "0"), # 전체
            "PRC1": kwargs.get("prc1", ""),          # 가격 필터 시작 (없음)
            "PRC2": kwargs.get("prc2", ""),          # 가격 필터 종료 (없음)
            "KEYB": kwargs.get("keyb", ""),          # NEXT KEY
        }
        return self._call_analysis_api("HHDFS76310010", "/uapi/overseas-stock/v1/ranking/trade-vol", params)

    def get_trade_pbmn(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        6. 해외주식 거래대금순위
        (TR_ID: HHDFS76320010)
        """
        params = {
            "AUTH": "",
            "EXCD": excd,
            "NDAY": kwargs.get("nday", "0"),       # 당일
            "VOL_RANG": kwargs.get("vol_rang", "0"), # 전체
            "PRC1": kwargs.get("prc1", ""),          # 가격 필터 시작 (없음)
            "PRC2": kwargs.get("prc2", ""),          # 가격 필터 종료 (없음)
            "KEYB": kwargs.get("keyb", ""),          # NEXT KEY
        }
        return self._call_analysis_api("HHDFS76320010", "/uapi/overseas-stock/v1/ranking/trade-pbmn", params)

    def get_trade_growth(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        7. 해외주식 거래증가율순위
        (TR_ID: HHDFS76330000)
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
        return self._call_analysis_api("HHDFS76330000", "/uapi/overseas-stock/v1/ranking/trade-growth", params)

    def get_trade_turnover(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        8. 해외주식 거래회전율순위
        (TR_ID: HHDFS76340000)
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
        return self._call_analysis_api("HHDFS76340000", "/uapi/overseas-stock/v1/ranking/trade-turnover", params)

    def get_domestic_volume_rank(self, market: str = "J", rank_type: str = "0", **kwargs) -> Dict[str, Any]:
        """
        [국내주식] 순위분석 > 거래량/거래금액 순위
        (TR_ID: FHPST01710000)
        
        :param market: 시장 분류 ("J": KRX/KOSPI, "Q": KOSDAQ - 내부적으로 변환 필요)
        :param rank_type: "0": 거래량순, "3": 거래금액순
        """
        # market 코드가 KOSPI(J), KOSDAQ(Q) 등으로 올 수 있음. API 스펙에 맞춰 조정.
        # volume_rank API는 J(KRX), NX(NXT) 등을 받음.
        params = {
            "FID_COND_MRKT_DIV_CODE": market if market in ["J", "NX", "UN", "W"] else "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000", # 전체
            "FID_DIV_CLS_CODE": "0",   # 전체
            "FID_BLNG_CLS_CODE": rank_type,
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "0000000000",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": ""
        }
        params.update(kwargs)
        return self._call_analysis_api("FHPST01710000", "/uapi/domestic-stock/v1/quotations/volume-rank", params)
