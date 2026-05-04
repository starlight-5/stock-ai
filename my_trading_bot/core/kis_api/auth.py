# -*- coding: utf-8 -*-
"""
한국투자증권(KIS) API 인증 관련 모듈입니다.
"""
import json
import logging
import requests
from typing import Dict, Any
from .base import KISBaseClient

logger = logging.getLogger(__name__)

class KISAuthHandler(KISBaseClient):
    """
    접근토큰 및 웹소켓 접속키 발급 등을 담당하는 인증 핸들러 클래스입니다.
    단일 책임 원칙(SRP)에 따라 KIS API 인증 처리만을 담당합니다.
    """
    
    def issue_access_token(self) -> Dict[str, Any]:
        """
        [인증] OAuth 접근토큰을 발급받습니다.
        유효기간 1일의 접근 토큰을 받아오며, 이후의 API 호출 시 헤더에 포함하여 사용합니다.
        
        Returns:
            Dict[str, Any]: 접근 토큰(access_token)을 포함한 API 응답 데이터 딕셔너리
        """
        api_url = "/oauth2/tokenP"
        url = f"{self.base_url}{api_url}"
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8"
        }
        
        Body = {
            "grant_type": "client_credentials",
            "appkey": self.appkey,
            "appsecret": self.appsecret,
        }

        try:
            # POST 요청을 통해 토큰 발급 진행
            response = requests.post(url, data=json.dumps(Body), headers=headers)
            
            if response.status_code == 200:
                logger.info("OAuth 접근토큰 발급 성공")
                return response.json()
            else:
                logger.error(f"API 호출 실패 (접근토큰): {response.status_code} - {response.text}")
                return {}
                
        except Exception as e:
            logger.error(f"요청 중 예외 발생 (접근토큰): {str(e)}")
            return {}

    def issue_ws_token(self) -> Dict[str, Any]:
        """
        [인증] WebSocket 실시간 데이터 접속키를 발급받습니다.
        실시간 체결, 호가 등의 웹소켓 통신을 시작하기 위해 필요한 approval_key를 발급받습니다.
        
        Returns:
            Dict[str, Any]: 웹소켓 접속키(approval_key)를 포함한 API 응답 데이터 딕셔너리
        """
        api_url = "/oauth2/Approval"
        url = f"{self.base_url}{api_url}"
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8"
        }
        
        Body = {
            "grant_type": "client_credentials",
            "appkey": self.appkey,
            "secretkey": self.appsecret, # 웹소켓 발급 시 파라미터명은 'secretkey' 임을 유의
        }

        try:
            # POST 요청을 통해 웹소켓 접속키 발급 진행
            response = requests.post(url, data=json.dumps(Body), headers=headers)
            
            if response.status_code == 200:
                logger.info("WebSocket 접속키 발급 성공")
                return response.json()
            else:
                logger.error(f"API 호출 실패 (웹소켓 접속키): {response.status_code} - {response.text}")
                return {}
                
        except Exception as e:
            logger.error(f"요청 중 예외 발생 (웹소켓 접속키): {str(e)}")
            return {}
