# -*- coding: utf-8 -*-
"""
한국투자증권(KIS) API 통신을 위한 기본 클래스 모듈입니다.
"""
import os
import logging
from typing import Dict
from dotenv import load_dotenv, find_dotenv

logger = logging.getLogger(__name__)

# .env 파일 로드
load_dotenv(find_dotenv())

class KISBaseClient:
    """
    모든 KIS API 핸들러의 기본이 되는 클래스입니다.
    기본 URL과 공통 헤더 생성 기능을 제공합니다.
    """
    
    # 실전 및 모의 투자에 대한 Base URL
    BASE_URL_REAL = os.getenv("BASE_URL_REAL")
    BASE_URL_DEMO = os.getenv("BASE_URL_DEMO")
    
    def __init__(self, appkey: str, appsecret: str, env_dv: str = "real", access_token: str = ""):
        """
        KISBaseClient 인스턴스를 초기화합니다.
        
        Args:
            appkey (str): 발급받은 앱키 (App Key)
            appsecret (str): 발급받은 앱시크릿키 (App Secret)
            env_dv (str): 환경 구분 ('real': 실전 투자, 'demo': 모의 투자)
            access_token (str): 인증 토큰 (기본값: "")
        """
        self.appkey = appkey
        self.appsecret = appsecret
        self.env_dv = env_dv
        self.access_token = access_token
        
        # 환경 구분에 따른 엔드포인트 URL 설정
        if self.env_dv == "real":
            self.base_url = self.BASE_URL_REAL
        elif self.env_dv == "demo":
            self.base_url = self.BASE_URL_DEMO
        else:
            logger.error("env_dv는 'real' 또는 'demo'여야 합니다.")
            raise ValueError("env_dv must be 'real' or 'demo'")

    def _get_headers(self, tr_id: str) -> Dict[str, str]:
        """
        API 호출 시 필요한 공통 헤더를 생성합니다.
        
        Args:
            tr_id (str): 거래 ID (Transaction ID)
            
        Returns:
            Dict[str, str]: HTTP 요청 헤더
        """
        if not self.access_token:
            logger.warning("access_token이 설정되지 않았습니다. API 호출이 실패할 수 있습니다. issue_access_token()을 먼저 호출하거나 토큰을 주입해주세요.")
            
        return {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.appkey,
            "appsecret": self.appsecret,
            "tr_id": tr_id,
            "custtype": "P" # P: 개인, B: 법인
        }
