# -*- coding: utf-8 -*-
"""
수집된 매매 데이터(trading_data_for_ai.csv)를 바탕으로 
XGBoost 모델을 학습시켜 진입 신호의 성공 확률을 예측하는 스크립트입니다.
"""

import pandas as pd
import numpy as np
try:
    from xgboost import XGBClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, confusion_matrix
except ImportError:
    print("오류: 필요한 라이브러리가 설치되어 있지 않습니다.")
    print("pip install xgboost scikit-learn pandas numpy 명령어를 실행하세요.")
    exit()

import os

def train():
    # 1. 데이터 로드
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base_dir, "trading_data_for_ai.csv")
    
    if not os.path.exists(csv_path):
        print(f"오류: {csv_path} 파일이 없습니다. 백테스트를 먼저 실행하여 데이터를 수집하세요.")
        return

    df = pd.read_csv(csv_path)
    
    # 데이터가 너무 적으면 학습이 무의미할 수 있음
    if len(df) < 50:
        print(f"경고: 데이터가 너무 적습니다 ({len(df)}개). 최소 200개 이상의 데이터가 필요합니다.")

    # 2. 피처(X)와 라벨(y) 분리
    # 종목(symbol) 정보는 제외하여 일반적인 시장 패턴을 학습하도록 함
    features = ["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]
    X = df[features]
    y = df["label"]

    # 3. 학습/테스트 데이터 분할 (8:2)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print(f"\n" + "="*50)
    print(f"      SMC AI FILTER MODEL TRAINING")
    print("="*50)
    print(f"총 데이터 수: {len(df)}개")
    print(f"학습용 데이터: {len(X_train)}개")
    print(f"검증용 데이터: {len(X_test)}개")
    print(f"라벨 분포 (성공 1 / 실패 0):\n{y.value_counts(normalize=True)}")
    print("-" * 50)

    # 4. XGBoost 모델 설정 및 학습
    # 하이퍼파라미터는 데이터 양에 따라 조정 가능
    model = XGBClassifier(
        n_estimators=100,
        learning_rate=0.05,
        max_depth=4,
        random_state=42,
        use_label_encoder=False,
        eval_metric='logloss'
    )

    model.fit(X_train, y_train)

    # 5. 검증 데이터로 모델 평가
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1] # 성공(1) 확률

    print("\n[1] 모델 성능 평가 (Classification Report)")
    print(classification_report(y_test, y_pred))

    print("\n[2] 피처 중요도 (Feature Importance)")
    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1]
    for i in range(len(features)):
        print(f"{i+1}. {features[indices[i]]:<15}: {importances[indices[i]]:.4f}")

    # 6. 모델 저장
    model_path = os.path.join(base_dir, "smc_ai_filter.json")
    model.save_model(model_path)
    print("\n" + "="*50)
    print(f"성공: AI 필터 모델이 {model_path}에 저장되었습니다.")
    print("="*50)

if __name__ == "__main__":
    train()
