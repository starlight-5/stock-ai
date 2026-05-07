# -*- coding: utf-8 -*-
"""
한국투자증권(KIS) API 인증 핸들러

이 모듈은 자동매매 시스템이 한국투자증권 서버와 안전하게 통신할 수 있도록 '출입증'을 발급받는 역할을 합니다.

[주요 기능 설명]
1. OAuth 접근토큰 발급 (issue_access_token):
   - 주식 주문, 잔고 조회 등 모든 일반 API 호출에 필요한 임시 비밀번호(Token)를 가져옵니다.
   - 유효기간은 24시간이며, 시스템 시작 시 자동으로 갱신됩니다.

2. 웹소켓 접속키 발급 (issue_ws_token):
   - 실시간 주가 데이터나 내 계좌의 체결 소식을 실시간으로 듣기 위한 전용 열쇠(Approval Key)를 가져옵니다.

3. 에러 핸들링:
   - 인증 실패 시 단순한 빈 값을 반환하는 대신, 상세한 에러 코드와 메시지를 반환하여
     매니저가 1분 제한(EGW00133) 등에 적절히 대응(대기 후 재시도)할 수 있도록 설계되었습니다.
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
                return {"error": response.text, "status_code": response.status_code}
                
        except Exception as e:
            logger.error(f"요청 중 예외 발생 (접근토큰): {str(e)}")
            return {"error": str(e)}

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
                error_msg = f"API 호출 실패 (웹소켓 접속키): {response.status_code} - {response.text}"
                logger.error(error_msg)
                return {"error": response.text, "status_code": response.status_code}
                
        except Exception as e:
            logger.error(f"요청 중 예외 발생 (웹소켓 접속키): {str(e)}")
            return {"error": str(e)}
