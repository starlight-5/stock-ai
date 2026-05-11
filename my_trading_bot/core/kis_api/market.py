from typing import Dict, Any, Optional
from .base import KISBaseClient

class KISMarketHandler(KISBaseClient):
    """
    해외주식 기본 시세(Market Data) 관련 API 호출을 담당하는 핸들러
    SRP(단일 책임 원칙)에 따라 시세 조회 기능만 캡슐화합니다.
    """

    def __init__(self, appkey: str, appsecret: str, env_dv: str = "real", access_token: str = ""):
        super().__init__(appkey, appsecret, env_dv)
        self.access_token = access_token

    def _call_market_api(self, path: str, tr_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        시세 API 공통 호출 메서드 (GET)
        """
        headers = self._get_headers(tr_id)
        return self._get(path, headers=headers, params=params)

    # 1. 해외주식 현재가상세
    def get_price_detail(self, excd: str, symb: str) -> Dict[str, Any]:
        """
        해외주식 현재가 상세 조회
        :param excd: 거래소코드 (NAS, NYS, AMS 등)
        :param symb: 종목코드 (AAPL 등)
        """
        path = "/uapi/overseas-price/v1/quotations/price-detail"
        tr_id = "HHDFS76200200"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "SYMB": symb
        }
        return self._call_market_api(path, tr_id, params)

    # 2. 해외주식 현재가 호가
    def get_asking_price(self, excd: str, symb: str) -> Dict[str, Any]:
        """
        해외주식 현재가 호가(1호가) 조회
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-asking-price"
        tr_id = "HHDFS76200100"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "SYMB": symb
        }
        return self._call_market_api(path, tr_id, params)

    # 3. 해외주식 현재체결가
    def get_price(self, excd: str, symb: str) -> Dict[str, Any]:
        """
        해외주식 현재체결가 조회
        """
        path = "/uapi/overseas-price/v1/quotations/price"
        tr_id = "HHDFS00000300"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "SYMB": symb
        }
        return self._call_market_api(path, tr_id, params)

    # 4. 해외주식 체결추이
    def get_quot_ccnl(self, excd: str, symb: str, tday: str = "", keyb: str = "") -> Dict[str, Any]:
        """
        해외주식 체결추이 조회
        :param tday: 당일 날짜 (YYYYMMDD) 등 특정 일자, 없으면 빈 문자열
        :param keyb: NEXT KEY
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-ccnl"
        tr_id = "HHDFS76200300"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "SYMB": symb,
            "TDAY": tday,
            "KEYB": keyb
        }
        return self._call_market_api(path, tr_id, params)

    # 5. 해외주식분봉조회
    def get_time_itemchartprice(self, excd: str, symb: str, nmin: str = "01", pinc: str = "1", 
                                ncnt: str = "30", dtm: str = "", keyb: str = "") -> Dict[str, Any]:
        """
        해외주식 분봉 조회
        :param nmin: 분구분 (01~99)
        :param pinc: 1(당일)
        :param ncnt: 요청건수 (최대 120)
        :param dtm: 조회대상일자/시간 (YYYYMMDDHHMMSS)
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        tr_id = "HHDFS76950200"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "SYMB": symb,
            "NMIN": nmin,
            "PINC": pinc,
            "NCNT": ncnt,
            "NEXT": dtm, # NEXT(KEYB) OR DTM
            "KEYB": keyb,
            "FILL": "Y",  # 필수 필드: 빈 구간 채우기 여부 (Y: 채움)
            "NREC": "120"
        }
        return self._call_market_api(path, tr_id, params)

    # 6. 해외주식 기간별시세
    def get_dailyprice(self, excd: str, symb: str, gubn: str = "0", modp: str = "0", 
                       tday: str = "", keyb: str = "") -> Dict[str, Any]:
        """
        해외주식 기간별시세 (일별체결)
        :param gubn: 0:일, 1:주, 2:월
        :param modp: 0:수정주가반영, 1:수정주가미반영
        """
        path = "/uapi/overseas-price/v1/quotations/dailyprice"
        tr_id = "HHDFS76240000"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "SYMB": symb,
            "GUBN": gubn,
            "BYMD": tday, # 날짜
            "MODP": modp,
            "KEYB": keyb
        }
        return self._call_market_api(path, tr_id, params)

    # 7. 해외주식 종목 기간별시세
    def get_daily_chartprice(self, excd: str, symb: str, gubn: str = "0", modp: str = "1", 
                             bymd: str = "", dtm: str = "", keyb: str = "") -> Dict[str, Any]:
        """
        해외주식 종목/지수/환율 기간별시세 (일/주/월/년)
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
        tr_id = "FHKST03030100"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "SYMB": symb,
            "GUBN": gubn,
            "BYMD": bymd,
            "MODP": modp,
            "NEXT": dtm,
            "KEYB": keyb
        }
        return self._call_market_api(path, tr_id, params)

    # 8. 해외주식조건검색
    def get_inquire_search(self, excd: str, prcs: str, prce: str, vol: str, 
                           amt: str, rate: str, rate2: str) -> Dict[str, Any]:
        """
        해외주식 조건검색
        :param prcs: 시작가격
        :param prce: 종료가격
        :param vol: 최소거래량
        :param amt: 최소거래대금
        :param rate: 최소등락율
        :param rate2: 최대등락율
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-search"
        tr_id = "HHDFS76410000"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "PRCS": prcs,
            "PRCE": prce,
            "VOL": vol,
            "AMT": amt,
            "RATE": rate,
            "RATE2": rate2
        }
        return self._call_market_api(path, tr_id, params)

    # 9. 해외결제일자조회
    def get_countries_holiday(self, dt: str, excd: str) -> Dict[str, Any]:
        """
        해외결제일자조회 (휴장일 포함)
        :param dt: 기준일자 (YYYYMMDD)
        """
        path = "/uapi/overseas-stock/v1/quotations/countries-holiday"
        tr_id = "CTOS5011R"
        params = {
            "BASS_DT": dt,
            "CTX_AREA_NK": "",
            "CTX_AREA_FK": "",
            "TR_ID": tr_id,
            "NATN_CD": excd # 국가코드 대신 거래소 코드가 들어가는지 확인
        }
        return self._call_market_api(path, tr_id, params)

    # 10. 해외주식 상품기본정보
    def get_search_info(self, prdt_type: str, prdt_cd: str) -> Dict[str, Any]:
        """
        해외주식 상품기본정보
        :param prdt_type: 상품유형 (예: 512 해외주식)
        :param prdt_cd: 상품코드
        """
        path = "/uapi/overseas-price/v1/quotations/search-info"
        tr_id = "CTPF1702R"
        params = {
            "PRDT_TYPE_CD": prdt_type,
            "PDNO": prdt_cd
        }
        return self._call_market_api(path, tr_id, params)

    # 11. 해외주식 업종별시세
    def get_industry_theme(self, excd: str, iscd: str) -> Dict[str, Any]:
        """
        해외주식 업종별시세
        :param iscd: 업종코드
        """
        path = "/uapi/overseas-price/v1/quotations/industry-theme"
        tr_id = "HHDFS76370000"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "ISCD": iscd
        }
        return self._call_market_api(path, tr_id, params)

    # 12. 해외주식 업종별코드조회
    def get_industry_price(self, excd: str, gb1: str = "0") -> Dict[str, Any]:
        """
        해외주식 업종별코드조회
        :param gb1: 0:전체, 1:업종
        """
        path = "/uapi/overseas-price/v1/quotations/industry-price"
        tr_id = "HHDFS76370100"
        params = {
            "AUTH": "",
            "EXCD": excd,
            "GB1": gb1
        }
        return self._call_market_api(path, tr_id, params)

    # 13. [국내주식] 주식일별분봉조회
    def get_domestic_minute_chart(self, symbol: str, time: str = "153000", date: str = "", **kwargs) -> Dict[str, Any]:
        """
        [국내주식] 주식일별분봉조회 (과거 일자 가능)
        (TR_ID: FHKST03010230)
        
        :param symbol: 종목코드 (6자리)
        :param time: 조회 시간 (HHMMSS)
        :param date: 조회 일자 (YYYYMMDD), 비어있으면 오늘 날짜 사용 권장 (백테스트용)
        """
        path = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
        tr_id = "FHKST03010230"
        if self.env_dv == "demo":
            tr_id = "VHKST03010230"
            
        # date가 없으면 오늘 날짜 문자열 생성 (하지만 백테스트에서는 보통 넘겨줌)
        if not date:
            import datetime
            date = datetime.datetime.now().strftime("%Y%m%d")
            
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", # KRX
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": time,
            "FID_INPUT_DATE_1": date,
            "FID_PW_DATA_INCU_YN": kwargs.get("fid_pw_data_incu_yn", "Y"), # 과거 데이터 포함 여부
            "FID_FAKE_TICK_INCU_YN": kwargs.get("fid_fake_tick_incu_yn", "")
        }
        return self._call_market_api(path, tr_id, params)

    # 14. [국내주식] 주식 일별 시세
    def get_domestic_daily_price(self, symbol: str, period_code: str = "D", adj_price_yn: str = "Y") -> Dict[str, Any]:
        """
        [국내주식] 주식 일별 시세 조회 (고가, 저가, 종가 등)
        (TR_ID: FHKST01010400)
        
        :param symbol: 종목코드 (6자리)
        :param period_code: 기간구분 (D:일, W:주, M:월)
        :param adj_price_yn: 수정주가 여부 (Y:적용, N:미적용)
        """
        path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        tr_id = "FHKST03010100" # 주식 당일/전일/시간외 챠트 데이터
        if self.env_dv == "demo":
            tr_id = "VHKST03010100"
            
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": "19900101", # 시작일 (충분히 과거로 설정)
            "FID_INPUT_DATE_2": "20991231", # 종료일
            "FID_PERIOD_DIV_CODE": period_code,
            "FID_ORG_ADJ_PRC": "0" if adj_price_yn == "Y" else "1"
        }
        return self._call_market_api(path, tr_id, params)
