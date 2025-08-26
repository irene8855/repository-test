# train_model.py
import argparse
import sqlite3
import json
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

def load_signals_from_db(db_path: str, table: str = "signals"):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    conn.close()
    return df

def explode_features_column(df: pd.DataFrame, col='features_json'):
    feats = []
    for v in df[col].fillna("{}"):
        try:
            d = json.loads(v)
            if not isinstance(d, dict):
                d = {}
        except Exception:
            d = {}
        feats.append(d)
    feats_df = pd.DataFrame(feats)
    # Prefix to avoid collisions
    feats_df.columns = [str(c) for c in feats_df.columns]
    return pd.concat([df.reset_index(drop=True), feats_df.reset_index(drop=True)], axis=1)

def build_feature_matrix(df: pd.DataFrame):
    # We take a set of typical features present in your pipeline.
    # If a column doesn't exist it will be filled with 0.
    use_cols = [
        "exp_pnl", "net_pnl", "entry_sell_units", "buy_amount_token_units", "exit_units_est",
        "hold_seconds",
        # ds features commonly present in features_json
        "liquidity_usd", "buys", "sells", "vol_m5", "avg_m5", "momentum_m5",
        # derivative-like features (if present)
        "d_price", "dd_price", "d_vol", "d_buys", "vol_rel_change"
    ]

    # If ts exists, add hour/minute
    if "ts" in df.columns:
        try:
            times = pd.to_datetime(df["ts"], errors="coerce")
            df["ts_hour"] = times.dt.hour.fillna(0).astype(int)
            df["ts_minute"] = times.dt.minute.fillna(0).astype(int)
            use_cols += ["ts_hour", "ts_minute"]
        except Exception:
            pass

    # ensure columns exist
    for c in use_cols:
        if c not in df.columns:
            df[c] = 0.0

    X = df[use_cols].fillna(0.0).astype(float)
    return X, use_cols

class ModelWrapper:
    """
    Обёртка: хранит обученную модель (lgb/xgb) и список колонок.
    Метод predict(X_df) должен вернуть массив вероятностей класса 1 (shape = (n,))
    """
    def __init__(self, model, feature_columns):
        self.model = model
        self.feature_columns = feature_columns

    def predict(self, X_df):
        # гарантируем DataFrame и все колонки в нужном порядке
        import numpy as np
        import pandas as pd
        if not isinstance(X_df, pd.DataFrame):
            X_df = pd.DataFrame(X_df)
        X = X_df.reindex(columns=self.feature_columns, fill_value=0.0)
        # LightGBM и XGBoost имеют predict_proba
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(X)
            # вернуть вероятность позитивного класса
            if proba.ndim == 2 and proba.shape[1] >= 2:
                return np.asarray(proba[:, 1])
            else:
                # fallback
                return np.asarray(proba).ravel()
        # если модель выдаёт raw score, попытаемся через sigmoid
        if hasattr(self.model, "predict"):
            raw = self.model.predict(X)
            raw = np.asarray(raw).ravel()
            # sigmoid
            probs = 1.0 / (1.0 + np.exp(-raw))
            return probs
        raise RuntimeError("Model has neither predict_proba nor predict")

def train_lightgbm(X_train, y_train, X_val=None, y_val=None, params=None):
    import lightgbm as lgb
    dtrain = lgb.Dataset(X_train, label=y_train)
    valid_sets = [dtrain]
    valid_names = ["train"]
    if X_val is not None and y_val is not None:
        dvalid = lgb.Dataset(X_val, label=y_val)
        valid_sets.append(dvalid)
        valid_names.append("valid")
    params = params or {
        "objective": "binary",
        "metric": "auc",
        "verbosity": -1,
        "boosting_type": "gbdt",
        "num_threads": 4,
        "seed": 42,
    }
    bst = lgb.train(params, dtrain, num_boost_round=500, valid_sets=valid_sets,
                    early_stopping_rounds=50, verbose_eval=50)
    return bst

def train_xgboost(X_train, y_train, X_val=None, y_val=None, params=None):
    import xgboost as xgb
    dtrain = xgb.DMatrix(X_train, label=y_train)
    evals = [(dtrain, "train")]
    deval = None
    if X_val is not None and y_val is not None:
        deval = xgb.DMatrix(X_val, label=y_val)
        evals.append((deval, "valid"))
    params = params or {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "verbosity": 1,
        "seed": 42,
    }
    bst = xgb.train(params, dtrain, num_boost_round=500, evals=evals,
                    early_stopping_rounds=50)
    return bst

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="signals.db", help="SQLite path")
    p.add_argument("--out", default="model_lgb.pkl", help="Output model path (joblib)")
    p.add_argument("--use-xgb", action="store_true", help="Train XGBoost instead of LightGBM")
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--min-samples", type=int, default=50, help="Min positive+negative samples required")
    args = p.parse_args()

    df = load_signals_from_db(args.db)
    if df is None or df.shape[0] == 0:
        raise SystemExit("No rows in DB. Make sure signals.db exists and has signals table.")

    # keep only labeled outcomes 0 or 1
    df = df[df["outcome"].isin([0,1])]
    if df.shape[0] == 0:
        raise SystemExit("No labeled rows (outcome 0/1) found in signals table. Need data to train.")

    # explode features_json -> columns
    df2 = explode_features_column(df, col='features_json')

    # build X, y
    X, feature_columns = build_feature_matrix(df2)
    y = df2["outcome"].astype(int).values

    # check samples
    if X.shape[0] < args.min_samples:
        raise SystemExit(f"Too few samples for training: {X.shape[0]} rows (<{args.min_samples}). Collect more labeled data.")

    # split
    from sklearn.model_selection import train_test_split
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=args.test_size, stratify=y, random_state=42)

    print("Train shape:", X_train.shape, "Val shape:", X_val.shape)
    if args.use_xgb:
        print("Training XGBoost...")
        model = train_xgboost(X_train, y_train, X_val, y_val)
    else:
        print("Training LightGBM...")
        model = train_lightgbm(X_train, y_train, X_val, y_val)

    # wrap and save
    wrapper = ModelWrapper(model, feature_columns)
    outp = Path(args.out)
    joblib.dump(wrapper, str(outp))
    print(f"Saved wrapped model to {outp}. Feature columns: {feature_columns}")

if __name__ == "__main__":
    main()
  
