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
        headers = self.get_common_headers(tr_id)
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
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
        return self._call_analysis_api("HHDFS76310010", "/uapi/overseas-stock/v1/ranking/trade-vol", params)

    def get_trade_pbmn(self, excd: str = "NAS", **kwargs) -> Dict[str, Any]:
        """
        6. 해외주식 거래대금순위
        (TR_ID: HHDFS76320010)
        """
        params = {"AUTH": "", "EXCD": excd, **kwargs}
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
