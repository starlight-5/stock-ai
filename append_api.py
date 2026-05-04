code_to_append = """
    # ==========================================
    # 기본 시세 (Market Data) 기능 위임
    # ==========================================

    def get_price_detail(self, excd: str, symb: str) -> Dict[str, Any]:
        \"\"\"해외주식 현재가상세\"\"\"
        return self._market.get_price_detail(excd, symb)

    def get_asking_price(self, excd: str, symb: str) -> Dict[str, Any]:
        \"\"\"해외주식 현재가 호가(1호가)\"\"\"
        return self._market.get_asking_price(excd, symb)

    def get_price(self, excd: str, symb: str) -> Dict[str, Any]:
        \"\"\"해외주식 현재체결가\"\"\"
        return self._market.get_price(excd, symb)

    def get_quot_ccnl(self, excd: str, symb: str, tday: str = "", keyb: str = "") -> Dict[str, Any]:
        \"\"\"해외주식 체결추이\"\"\"
        return self._market.get_quot_ccnl(excd, symb, tday, keyb)

    def get_time_itemchartprice(self, excd: str, symb: str, nmin: str = "01", pinc: str = "1", 
                                ncnt: str = "30", dtm: str = "", keyb: str = "") -> Dict[str, Any]:
        \"\"\"해외주식 분봉조회\"\"\"
        return self._market.get_time_itemchartprice(excd, symb, nmin, pinc, ncnt, dtm, keyb)

    def get_dailyprice(self, excd: str, symb: str, gubn: str = "0", modp: str = "0", 
                       tday: str = "", keyb: str = "") -> Dict[str, Any]:
        \"\"\"해외주식 기간별시세\"\"\"
        return self._market.get_dailyprice(excd, symb, gubn, modp, tday, keyb)

    def get_daily_chartprice(self, excd: str, symb: str, gubn: str = "0", modp: str = "1", 
                             bymd: str = "", dtm: str = "", keyb: str = "") -> Dict[str, Any]:
        \"\"\"해외주식 종목 기간별시세\"\"\"
        return self._market.get_daily_chartprice(excd, symb, gubn, modp, bymd, dtm, keyb)

    def get_inquire_search(self, excd: str, prcs: str, prce: str, vol: str, 
                           amt: str, rate: str, rate2: str) -> Dict[str, Any]:
        \"\"\"해외주식 조건검색\"\"\"
        return self._market.get_inquire_search(excd, prcs, prce, vol, amt, rate, rate2)

    def get_countries_holiday(self, dt: str, excd: str) -> Dict[str, Any]:
        \"\"\"해외결제일자조회\"\"\"
        return self._market.get_countries_holiday(dt, excd)

    def get_search_info(self, prdt_type: str, prdt_cd: str) -> Dict[str, Any]:
        \"\"\"해외주식 상품기본정보\"\"\"
        return self._market.get_search_info(prdt_type, prdt_cd)

    def get_industry_theme(self, excd: str, iscd: str) -> Dict[str, Any]:
        \"\"\"해외주식 업종별시세\"\"\"
        return self._market.get_industry_theme(excd, iscd)

    def get_industry_price(self, excd: str, gb1: str = "0") -> Dict[str, Any]:
        \"\"\"해외주식 업종별코드조회\"\"\"
        return self._market.get_industry_price(excd, gb1)
"""

with open(r'c:\simpleStock\my_trading_bot\core\api_handler.py', 'a', encoding='utf-8') as f:
    f.write(code_to_append)
