# -*- coding: utf-8 -*-
"""
뉴스 기반 스마트 종목 선택기 (NewsScanner)

매일 장 시작 전, KIS API의 세 가지 데이터 소스를 종합 분석하여
당일 투자에 적합한 종목을 동적으로 선별합니다.

[데이터 소스]
  1. 해외속보(brknews_title): 실시간 주요 속보, 종목 코드가 포함되어 있어
     특정 종목이 얼마나 화제인지 파악 가능
  2. 해외뉴스종합(news_title): 국가별 해외 뉴스 목록, 긍정/부정 키워드 감지
  3. 권리종합(rights_by_ice): 배당락일, 실적발표일 등 이벤트 일정 파악

[스코어 계산 방식]
  - 속보에 종목 언급: +3
  - 뉴스에 긍정 키워드 (beat, upgrade, surges 등): +2
  - 뉴스에 부정 키워드 (miss, downgrade, fraud 등): -3
  - 배당락일 7일 이내: +1
  - 실적발표일 ±3일 이내: +2 (변동성 확대 구간)
  - WATCHLIST 기본 우량주 보너스: +2

[위험 필터]
  - 속보/뉴스에 CPI, FOMC, 금리 관련 키워드 감지 시: 해당일 전 종목 진입 차단
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from .gemini_analyzer import GeminiAnalyzer

logger = logging.getLogger(__name__)



# ─────────────────────────────────────────────────────────────────────────────
# [스코어 계산 키워드 상수 정의]
# ─────────────────────────────────────────────────────────────────────────────

# 뉴스 제목에서 발견 시 +2점 (호재 신호)
POSITIVE_KEYWORDS = [
    "beats", "beat", "surges", "upgrade", "upgrades", "raises", "raise",
    "record", "strong", "growth", "profit", "buy", "outperform", "bullish",
    "rally", "gains", "soars", "jumps", "rises", "momentum",
    # 한국어 키워드
    "상향", "호실적", "성장", "매수", "상승", "급등", "어닝서프라이즈",
]

# 뉴스 제목에서 발견 시 -3점 (악재 신호)
NEGATIVE_KEYWORDS = [
    "misses", "miss", "downgrade", "downgrades", "cuts", "cut", "lawsuit",
    "fraud", "investigation", "recall", "warning", "loss", "decline", "drop",
    "falls", "sinks", "plunges", "sell", "underperform", "bearish", "layoffs",
    # 한국어 키워드
    "하향", "부진", "적자", "손실", "하락", "급락", "소송", "리콜",
]

# 이 키워드가 속보/뉴스에 등장하면 당일 모든 종목 진입 차단 (거시지표 발표일)
MACRO_RISK_KEYWORDS = [
    "CPI", "FOMC", "Federal Reserve", "Fed rate", "interest rate",
    "inflation", "GDP", "NFP", "Non-Farm", "payroll",
    # 한국어
    "소비자물가", "연준", "기준금리", "금리결정", "고용지표",
]


class NewsScanner:
    """
    KIS API 뉴스 데이터를 분석하여 당일 투자 적합 종목을 선별합니다.

    사용 예시:
        scanner = NewsScanner(api_handler)
        result = scanner.scan(watchlist=[("NAS", "NVDA"), ("NAS", "AAPL")])
        # result: [("NAS", "NVDA"), ("NAS", "AAPL")] (스코어 높은 순 정렬)
    """

    def __init__(self, api, top_n: int = 5):
        """
        :param api: KISApiHandler 인스턴스
        :param top_n: 최종 선별할 최대 종목 수
        """
        self._api = api
        self._top_n = top_n
        self._gemini = GeminiAnalyzer() # AI 분석기 초기화


    def scan(self, watchlist: Optional[List[Tuple[str, str]]] = None, target_date: Optional[str] = None) -> List[Tuple[str, str]]:
        """
        뉴스/권리 데이터를 종합하여 당일 투자 적합 종목을 '발굴'하여 반환합니다.

        :param watchlist: 기본 감시 종목 (없어도 무방)
        :param target_date: 조회할 날짜 (YYYYMMDD 형식, None이면 오늘)
        :return: 발굴된 스코어 상위 N개 종목 [(거래소코드, 종목코드), ...]
        """
        date_str = target_date or datetime.now().strftime("%Y%m%d")
        logger.info(f"[NewsScanner] {date_str} 뉴스 데이터 기반 종목 발굴 시작...")

        # 1. 해외속보 조회
        brknews_items, brknews_text = self._fetch_breaking_news(target_date=date_str)

        # 2. 해외뉴스종합 조회
        news_items, news_text = self._fetch_news_summary(target_date=date_str)

        # 3. 거시지표 리스크 확인
        if self._is_macro_risk_day(brknews_text, news_text):
            logger.warning(f"[NewsScanner] ⚠️ {date_str}: 거시지표 리스크 감지로 모든 진입 차단")
            return []

        # 4. 종목 발굴 및 스코어링
        scores: Dict[str, int] = {}
        excd_map: Dict[str, str] = {}
        
        # [기본] watchlist가 있다면 기본 보너스 부여
        if watchlist:
            for excd, sym in watchlist:
                scores[sym] = 2
                excd_map[sym] = excd

        # [단계 1] 속보 기반 종목 발굴 및 언급 횟수 점수 부여
        self._score_from_brknews(brknews_items, scores, excd_map)

        # [단계 2] 뉴스 키워드 감성 분석 (긍정/부정)
        # 키워드 분석을 통해 후보 종목을 1차 필터링
        self._score_from_news(news_items, scores, list(scores.keys()))

        # [추가] AI(Gemini) 정밀 분석 수행
        # 점수가 0점 이상인 종목들 중 상위 뉴스를 선별하여 AI에게 검증 요청
        self._refine_score_with_ai(news_items, scores)


        # [단계 3] 권리종합(실적/배당) 점수 반영
        # 발굴된 모든 종목에 대해 수행 (상위 스코어 종목 위주로 제한 가능하지만 일단 모두 수행)
        for sym in list(scores.keys()):
            # 점수가 어느 정도 있는 종목만 권리 정보 조회 (API 호출 절약)
            if scores[sym] >= 3:
                scores[sym] += self._get_rights_score(sym)

        # 5. 하이브리드 선정 로직 (핵심주 3 + 뉴스주 2)
        final_symbols = []
        
        # (1) 핵심 우량주 3개 선점 (WATCHLIST 상위 3개)
        if watchlist:
            core_count = 3
            for i in range(min(core_count, len(watchlist))):
                excd, sym = watchlist[i]
                final_symbols.append((excd, sym))
                logger.info(f"[NewsScanner] 하이브리드 - 핵심주 선정: {sym}")

        # (2) 뉴스 발굴 종목 중 핵심주와 중복되지 않는 상위 종목 추가
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        news_added_count = 0
        max_news_count = self._top_n - len(final_symbols) # 보통 2개

        for sym, score in sorted_scores:
            if news_added_count >= max_news_count:
                break
            
            # 이미 핵심주로 선정된 종목 제외
            if any(sym == f[1] for f in final_symbols):
                continue
            
            if score > 0:
                final_symbols.append((excd_map.get(sym, "NAS"), sym))
                news_added_count += 1
                logger.info(f"[NewsScanner] 하이브리드 - 뉴스주 선정: {sym} (점수: {score})")

        # (3) 만약 여전히 자리가 남는다면 (뉴스주 부족 시) WATCHLIST에서 추가
        if len(final_symbols) < self._top_n and watchlist:
            for excd, sym in watchlist:
                if len(final_symbols) >= self._top_n: break
                if not any(sym == f[1] for f in final_symbols):
                    final_symbols.append((excd, sym))

        return final_symbols




    def _refine_score_with_ai(self, news_items: List[Dict[str, Any]], scores: Dict[str, int]) -> None:
        """
        AI(Gemini)를 사용하여 뉴스의 문맥을 정밀 분석하고 점수를 보정합니다.
        """
        if not self._gemini.model: return

        # 분석 대상: 현재 스코어가 있는 종목들 중 상위 10개 뉴스 제목만 선별
        target_news = []
        target_symbols = [sym for sym, score in scores.items() if score > 0]
        
        for item in news_items:
            title = item.get("title", "")
            # 제목에 타겟 종목이 포함되어 있는지 확인
            for sym in target_symbols:
                if sym in title.upper():
                    target_news.append({"symbol": sym, "title": title})
                    break
            if len(target_news) >= 15: break # API 부하 방지를 위해 최대 15개로 제한

        if not target_news: return

        logger.info(f"[NewsScanner] AI에게 {len(target_news)}개 뉴스 정밀 분석 요청 중...")
        ai_results = self._gemini.analyze_sentiment(target_news)

        # AI 결과를 스코어에 반영 (점수 범위 -1.0 ~ 1.0 -> -10 ~ 10점으로 환산)
        for sym, ai_score in ai_results.items():
            if sym in scores:
                bonus = int(ai_score * 10)
                scores[sym] += bonus
                logger.info(f"[NewsScanner] AI 분석 결과 - {sym}: {ai_score} (보너스 {bonus}점 부여)")

    # ─────────────────────────────────────────────────────────────────────
    # [내부 메서드]
    # ─────────────────────────────────────────────────────────────────────


    def _fetch_breaking_news(self, target_date: str) -> Tuple[list, str]:
        """
        해외속보(brknews_title) API를 호출하여 특정 날짜의 속보 목록을 가져옵니다.
        """
        try:
            res = self._api.get_overseas_brknews(
                fid_news_ofer_entp_code="0",
                fid_cond_scr_div_code="11801",
                fid_input_date_1=target_date
            )

            items = res.get("output", [])
            if not items:
                logger.warning("[NewsScanner] 해외속보 데이터 없음")
                return [], ""
            # 전체 제목 합본 (키워드 매칭용)
            all_text = " ".join(
                item.get("hts_pbnt_titl_cntt", "") for item in items
            )
            logger.info(f"[NewsScanner] 해외속보 {len(items)}건 조회 완료")
            return items, all_text
        except Exception as e:
            logger.error(f"[NewsScanner] 해외속보 조회 실패: {e}")
            return [], ""

    def _fetch_news_summary(self, target_date: str) -> Tuple[list, str]:
        """
        해외뉴스종합(news_title) API를 호출하여 특정 날짜의 미국 뉴스 목록을 가져옵니다.
        """
        try:
            res = self._api.get_overseas_news_title(
                nation_cd="US",
                data_dt=target_date
            )

            items = res.get("outblock1", [])
            if not items:
                logger.warning("[NewsScanner] 해외뉴스종합 데이터 없음")
                return [], ""
            all_text = " ".join(
                item.get("title", "") for item in items
            )
            logger.info(f"[NewsScanner] 해외뉴스종합 {len(items)}건 조회 완료")
            return items, all_text
        except Exception as e:
            logger.error(f"[NewsScanner] 해외뉴스종합 조회 실패: {e}")
            return [], ""

    def _is_macro_risk_day(self, brknews_text: str, news_text: str) -> bool:
        """
        속보/뉴스 전체 텍스트에서 거시지표 발표 관련 키워드를 감지합니다.
        감지 시 True를 반환하여 당일 전 종목 진입을 차단합니다.
        """
        combined = (brknews_text + " " + news_text).upper()
        for keyword in MACRO_RISK_KEYWORDS:
            if keyword.upper() in combined:
                logger.warning(f"[NewsScanner] 거시지표 키워드 감지: '{keyword}'")
                return True
        return False

    def _score_from_brknews(
        self,
        items: list,
        scores: Dict[str, int],
        excd_map: Dict[str, str],
    ) -> None:
        """
        속보 항목을 분석하여 종목을 발굴하고 스코어를 부여합니다.
        iscd1~10 필드뿐만 아니라 제목 텍스트에서도 종목을 추출합니다.
        """
        mention_count: Dict[str, int] = {}
        from my_trading_bot.strategies.v1_smc.params import WATCHLIST

        for item in items:
            title = item.get("hts_pbnt_titl_cntt", "").upper()
            found_in_title = set()

            # 1. 제목에서 WATCHLIST 종목 직접 매칭 (정규표현식으로 단어 경계 확인)
            for _, sym in WATCHLIST:
                if re.search(r'\b' + re.escape(sym) + r'\b', title):
                    found_in_title.add(sym)

            # 2. iscd1~iscd10 필드 확인
            iscd_fields = [item.get(f"iscd{i}", "").strip() for i in range(1, 11) if item.get(f"iscd{i}", "").strip()]
            
            # 3. 종합하여 카운트
            all_syms = found_in_title.union(set(iscd_fields))
            for sym in all_syms:
                mention_count[sym] = mention_count.get(sym, 0) + 1

        # 스코어 반영 및 종목 발굴
        for sym, count in mention_count.items():
            point = min(count * 5, 15)  # 언급당 5점, 최대 15점
            if sym in scores:
                scores[sym] += point
                logger.info(f"[NewsScanner] {sym}: 속보 {count}회 언급 → +{point}점")
            else:
                # 새로운 종목 발굴 (알려진 우량주거나 언급 횟수가 많은 경우만 발굴)
                # 오탐 방지를 위해 2회 이상 언급되거나 WATCHLIST에 있는 경우만 신규 발굴
                is_known = any(sym == w[1] for w in WATCHLIST)
                if is_known or count >= 2:
                    scores[sym] = point
                    excd_map[sym] = "NAS"
                    logger.info(f"[NewsScanner] 신규 종목 발굴: {sym} (속보 {count}회 언급) → +{point}점")



    def _score_from_news(
        self,
        items: list,
        scores: Dict[str, int],
        symbols: List[str],
    ) -> None:
        """
        뉴스 제목에서 긍정/부정 키워드를 감지하여 관련 종목의 스코어를 조정합니다.
        종목 심볼이 뉴스 제목에 포함된 경우에만 키워드 스코어를 적용합니다.
        """
        from my_trading_bot.strategies.v1_smc.params import WATCHLIST
        for item in items:
            title = item.get("title", "").upper()
            symb = item.get("symb", "").strip()
            
            # [추가] 제목에서 WATCHLIST 종목 직접 매칭
            target_syms = [symb] if symb else []
            for _, sym in WATCHLIST:
                if sym in title:
                    target_syms.append(sym)

            # 해당 뉴스가 우리가 보는 종목과 관련 있는지 확인
            relevant_symbols = []
            for s in set(target_syms):
                if s in scores:
                    relevant_symbols.append(s)
            
            # 제목에서 직접 종목 코드 검색
            for sym in symbols:
                if re.search(r'\b' + re.escape(sym) + r'\b', title):
                    if sym not in relevant_symbols:
                        relevant_symbols.append(sym)

            if not relevant_symbols:
                continue

            # 긍정 키워드 검사
            for kw in POSITIVE_KEYWORDS:
                if kw.upper() in title:
                    for sym in relevant_symbols:
                        scores[sym] += 2
                        logger.info(f"[NewsScanner] {sym}: 긍정 키워드 '{kw}' 감지 → +2점")
                    break  # 종목당 긍정 1회만 적용

            # 부정 키워드 검사
            for kw in NEGATIVE_KEYWORDS:
                if kw.upper() in title:
                    for sym in relevant_symbols:
                        scores[sym] -= 3
                        logger.warning(f"[NewsScanner] {sym}: 부정 키워드 '{kw}' 감지 → -3점")
                    break  # 종목당 부정 1회만 적용

    def _get_rights_score(self, symb: str) -> int:
        """
        권리종합 API를 통해 배당락일, 실적발표일 이벤트를 조회하고
        현재 날짜와의 근접도에 따라 스코어를 부여합니다.

        :param symb: 종목코드
        :return: 스코어 (-2 ~ +2)
        """
        try:
            res = self._api.get_rights_by_ice(ncod="US", symb=symb)
            items = res.get("output1", [])
            if not items:
                return 0

            today = datetime.now().date()
            score = 0

            for item in items:
                ca_title = item.get("ca_title", "").lower()  # 권리유형

                # 배당락일 근접 여부 확인 (+1점)
                div_lock_dt = item.get("div_lock_dt", "").strip()
                if div_lock_dt and len(div_lock_dt) == 8:
                    try:
                        div_date = datetime.strptime(div_lock_dt, "%Y%m%d").date()
                        days_diff = (div_date - today).days
                        if 0 <= days_diff <= 7:
                            score += 1
                            logger.info(
                                f"[NewsScanner] {symb}: 배당락일 {days_diff}일 후 → +1점"
                            )
                    except ValueError:
                        pass

                # 실적발표 관련 권리유형 감지 (+2점)
                # 권리종합에는 직접적인 실적발표일 필드가 없으나,
                # ca_title에 'earnings', 'dividend' 등이 포함됨
                if any(kw in ca_title for kw in ["earnings", "split", "merger"]):
                    announce_dt = (
                        item.get("anno_dt", "")
                        or item.get("validity_dt", "")
                        or ""
                    ).strip()
                    if announce_dt and len(announce_dt) == 8:
                        try:
                            ann_date = datetime.strptime(announce_dt, "%Y%m%d").date()
                            days_diff = abs((ann_date - today).days)
                            if days_diff <= 3:
                                score += 2
                                logger.info(
                                    f"[NewsScanner] {symb}: 실적/이벤트 {days_diff}일 이내 → +2점"
                                )
                        except ValueError:
                            pass

            return score

        except Exception as e:
            logger.debug(f"[NewsScanner] {symb} 권리종합 조회 실패 (무시): {e}")
            return 0
