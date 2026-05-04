---
description: 프로젝트 구조 관리
---

프로젝트 구조
/my_trading_bot
│
├── main.py                 # 실행 파일 (여기서 v1을 쓸지 v2를 쓸지 결정)
├── config.yaml             # 어떤 전략을 활성화할지 적어두는 설정 파일
│
├── core/                   # 절대 바뀌지 않는 공통 엔진 (계좌 접속, 매수/매도 실행)
│   ├── api_handler.py      # KIS, Alpaca API 연결
│   └── logger.py           # 투자 결과 기록기
│
└── strategies/             # 투자 전략 '부품'들이 모인 곳
    ├── base.py             # 모든 전략이 지켜야 할 규칙 (추상 클래스)
    ├── v1_smc/             # [폴더] 예전 SMC 전략 관련 파일들
    │   ├── logic.py        # v1의 핵심 로직
    │   └── params.py       # v1 전용 설정값 (손절 폭 등)
    └── v2_high_vol/        # [폴더] 새로 만든 고변동성 전략 관련 파일들
        ├── logic.py        # v2의 핵심 로직
        └── params.py       # v2 전용 설정값