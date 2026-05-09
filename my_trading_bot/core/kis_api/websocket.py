from __future__ import annotations
import json
import asyncio
import websockets
from typing import Dict, Any, Callable, Awaitable, List, Optional
import logging

logger = logging.getLogger(__name__)

class KISWebSocketHandler:
    """
    한국투자증권 API 해외주식 실시간 시세 (WebSocket) 핸들러
    
    지원하는 실시간 데이터:
    1. 해외주식 실시간호가 (미국) - HDFSASP0
    2. 해외주식 실시간지연체결가 - HDFSCNT0
    3. 해외주식 실시간체결통보 - H0GSCNI0
    4. 국내주식 실시간체결가 - H0STCNT0
    5. 국내주식 실시간체결통보 - H0STCNI0
    """
    def __init__(self, approval_key: str = "", env_dv: str = "real"):
        self.approval_key = approval_key
        self.env_dv = env_dv
        
        # 웹소켓 운영 접속망 (실전/모의)
        if self.env_dv == "real":
            self.ws_url = "ws://ops.koreainvestment.com:21000"
        else:
            self.ws_url = "ws://ops.koreainvestment.com:31000"

    def _build_request(self, tr_id: str, tr_key: str, tr_type: str = "1") -> str:
        """
        웹소켓 구독/해지 요청 JSON 생성
        :param tr_id: 트랜잭션 ID
        :param tr_key: 종목코드 (D+종목코드 등) 또는 사용자 ID
        :param tr_type: "1" (등록), "2" (해제)
        """
        req = {
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",  # P: 개인, B: 법인 (기본 P 적용)
                "tr_type": tr_type,
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key
                }
            }
        }
        return json.dumps(req)
        
    def get_asking_price_req(self, symb: str, tr_type: str = "1") -> str:
        """1. 해외주식 실시간호가(미국) 구독 페이로드 반환"""
        return self._build_request("HDFSASP0", f"D{symb}", tr_type)

    def get_delayed_ccnl_req(self, symb: str, tr_type: str = "1") -> str:
        """2. 해외주식 실시간지연체결가 구독 페이로드 반환"""
        return self._build_request("HDFSCNT0", f"D{symb}", tr_type)

    def get_ccnl_notice_req(self, hts_id: str, tr_type: str = "1") -> str:
        """
        3. 해외주식 실시간체결통보 구독 페이로드 반환
        :param hts_id: 고객 HTS ID
        """
        return self._build_request("H0GSCNI0", hts_id, tr_type)

    def get_domestic_price_req(self, symb: str, tr_type: str = "1") -> str:
        """4. 국내주식 실시간체결가 구독 페이로드 반환"""
        return self._build_request("H0STCNT0", symb, tr_type)

    def get_domestic_ccnl_notice_req(self, hts_id: str, tr_type: str = "1") -> str:
        """5. 국내주식 실시간체결통보 구독 페이로드 반환"""
        return self._build_request("H0STCNI0", hts_id, tr_type)

    async def connect_and_listen(self, requests_payloads: List[str], callback: Callable[[str], Awaitable[None]], 
                                 new_req_queue: Optional[asyncio.Queue] = None):
        """
        웹소켓 서버에 연결하여 구독 요청을 보내고, 수신되는 실시간 데이터를 콜백 함수로 전달하는 메인 루프입니다.
        
        :param requests_payloads: 초기 구독할 요청 JSON 문자열 리스트
        :param callback: 데이터를 수신할 때마다 호출할 비동기(async) 콜백 함수
        :param new_req_queue: 실행 중 추가될 구독 요청을 담는 큐 (Optional)
        """
        if not self.approval_key:
            logger.error("웹소켓 접속키(approval_key)가 설정되지 않았습니다.")
            return

        # 활성 구독 관리 (중복 제거 및 재접속 시 재전송 목적)
        active_requests_dict = {}
        for req in requests_payloads:
            try:
                d = json.loads(req)
                tr_type = d.get("header", {}).get("tr_type", "1")
                key = (d["body"]["input"]["tr_id"], d["body"]["input"]["tr_key"])
                if tr_type == "1":
                    active_requests_dict[key] = req
                elif tr_type == "2":
                    active_requests_dict.pop(key, None)
            except Exception:
                pass

        while True:
            logger.info(f"웹소켓 연결 시도: {self.ws_url}")
            try:
                async with websockets.connect(self.ws_url, ping_interval=60) as websocket:
                    logger.info("웹소켓 연결 성공")
                    
                    # 기존 활성 구독 요청 모두 전송 (재접속 포함)
                    for req in active_requests_dict.values():
                        await websocket.send(req)
                        logger.info(f"구독 요청 전송: {req}")
                        
                    async def send_loop():
                        """새로운 구독 요청이 큐에 들어오면 웹소켓으로 전송하는 루프"""
                        if not new_req_queue:
                            return
                        while True:
                            req = await new_req_queue.get()
                            
                            # 활성 구독 리스트 업데이트
                            try:
                                d = json.loads(req)
                                tr_type = d.get("header", {}).get("tr_type", "1")
                                key = (d["body"]["input"]["tr_id"], d["body"]["input"]["tr_key"])
                                if tr_type == "1":
                                    active_requests_dict[key] = req
                                elif tr_type == "2":
                                    active_requests_dict.pop(key, None)
                            except Exception:
                                pass
                                
                            await websocket.send(req)
                            logger.info(f"추가 구독 요청 전송: {req}")
                            new_req_queue.task_done()

                    # 추가 요청 전송 루프를 백그라운드에서 실행
                    sender_task = asyncio.create_task(send_loop())

                    # 데이터 수신 루프
                    try:
                        while True:
                            data = await websocket.recv()
                            await callback(data)
                    except websockets.ConnectionClosed:
                        logger.warning("웹소켓 연결이 종료되었습니다. (Connection Closed) 5초 후 재연결합니다.")
                    finally:
                        sender_task.cancel()
                        
            except Exception as e:
                logger.error(f"웹소켓 통신 오류: {e}. 5초 후 재시도합니다.")
            
            # 재접속 전 5초 대기
            await asyncio.sleep(5)
