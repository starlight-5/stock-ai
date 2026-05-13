# -*- coding: utf-8 -*-
"""
Google Gemini AI를 이용한 주식 뉴스 감성 분석 모듈
"""

import os
import json
import logging
import google.generativeai as genai
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class GeminiAnalyzer:
    def __init__(self, api_key: Optional[str] = None):
        """
        :param api_key: Google Gemini API Key. None이면 환경변수 GEMINI_API_KEY 사용.
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("[GeminiAnalyzer] API Key가 설정되지 않았습니다. LLM 분석이 비활성화됩니다.")
            self.model = None
            return

        try:
            genai.configure(api_key=self.api_key)
            # 모델 명칭 명확화 (models/ 접두사 포함)
            self.model = genai.GenerativeModel('models/gemini-1.5-flash')
            logger.info("[GeminiAnalyzer] Gemini AI 모델(models/gemini-1.5-flash) 설정 완료")

        except Exception as e:
            logger.error(f"[GeminiAnalyzer] 모델 설정 실패: {e}")
            self.model = None

    def analyze_sentiment(self, news_items: List[Dict[str, str]]) -> Dict[str, float]:
        """
        뉴스 목록을 받아 각 뉴스의 주가 영향력 점수를 분석합니다.
        
        :param news_items: [{'title': '...', 'symbol': '...'}, ...]
        :return: {symbol: avg_score} (score 범위: -1.0 ~ 1.0)
        """
        if not self.model or not news_items:
            return {}

        # 1. 프롬프트 구성
        news_context = "\n".join([f"- [{item['symbol']}] {item['title']}" for item in news_items])
        
        prompt = f"""
        당신은 월스트리트의 전문 주식 분석가입니다. 
        아래 제공된 뉴스 제목들이 해당 종목의 주가에 미칠 단기적 영향(Sentiment)을 분석하여 점수를 매겨주세요.

        [분석 규칙]
        1. 점수는 -1.0(매우 부정적/폭락 예상)에서 1.0(매우 긍정적/폭등 예상) 사이의 소수점으로 답변하세요.
        2. 중립적이거나 영향이 모호하면 0.0을 부여하세요.
        3. 반드시 아래의 JSON 형식으로만 답변하세요. 다른 설명은 생략하세요.

        [응답 형식]
        {{
            "SYMBOL1": 0.85,
            "SYMBOL2": -0.4,
            ...
        }}

        [뉴스 목록]
        {news_context}
        """

        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    candidate_count=1,
                    stop_sequences=None,
                    max_output_tokens=1000,
                    temperature=0.1, # 일관된 분석을 위해 낮은 온도 설정
                )
            )

            # JSON 파싱 강화: 텍스트 내에서 JSON 블록({...})만 추출
            text = response.text
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                clean_text = json_match.group(0)
                result = json.loads(clean_text)
            else:
                # 정규식 실패 시 기존 방식 시도
                clean_text = text.replace("```json", "").replace("```", "").strip()
                result = json.loads(clean_text)
            
            logger.info(f"[GeminiAnalyzer] {len(result)}개 종목의 AI 정밀 분석 완료")
            return result


        except Exception as e:
            logger.error(f"[GeminiAnalyzer] 분석 중 오류 발생: {e}")
            return {}
