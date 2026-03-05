from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

FEATURE_NAMES = ["model_edge", "open_price", "abs_open_spread"]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def predict_p_cover_outcome(
    model_edge: float,
    open_price: float,
    open_spread: float,
    calibration_doc: Dict[str, Any],
) -> Optional[float]:
    """Predict calibrated outcome probability using stored logistic coefficients."""
    coeffs = calibration_doc.get("coefficients")
    intercept = calibration_doc.get("intercept")
    if coeffs is None or intercept is None:
        return None
    if len(coeffs) != len(FEATURE_NAMES):
        return None

    x1 = float(model_edge)
    x2 = float(open_price)
    x3 = abs(float(open_spread))
    z = float(intercept) + coeffs[0] * x1 + coeffs[1] * x2 + coeffs[2] * x3
    return float(_sigmoid(z))


def _prepare_outcome_dataset(
    picks: List[Dict[str, Any]],
    include_push_as_half: bool = False,
) -> Dict[str, Any]:
    X = []
    y = []
    pushes = 0
    dropped = 0
    used = 0
    for p in picks:
        result = p.get("result")
        if result == "PUSH":
            pushes += 1
            # LogisticRegression in sklearn expects binary targets.
            # We keep PUSH excluded from training dataset.
            if not include_push_as_half:
                continue
            continue
        if result not in ("WIN", "LOSS"):
            continue

        model_edge = p.get("model_edge")
        open_price = p.get("open_price")
        open_spread = p.get("open_spread")
        if model_edge is None or open_price is None or open_spread is None:
            dropped += 1
            continue

        X.append([float(model_edge), float(open_price), abs(float(open_spread))])
        y.append(1 if result == "WIN" else 0)
        used += 1

    return {
        "X": np.array(X),
        "y": np.array(y),
        "used": used,
        "pushes_seen": pushes,
        "dropped": dropped,
    }


async def fit_outcome_calibration(
    db,
    include_push_as_half: bool = False,
    min_samples: int = 50,
) -> Dict[str, Any]:
    """
    Fit binary outcome calibration on settled picks using LogisticRegression (L2).
    Features: [model_edge, open_price, abs(open_spread)]
    Label: WIN=1, LOSS=0, PUSH excluded (or 0.5 optional, implemented as exclusion by default).
    """
    query = {"result": {"$in": ["WIN", "LOSS", "PUSH"]}}
    picks = await db.predictions.find(query, {"_id": 0}).to_list(50000)

    dataset = _prepare_outcome_dataset(picks, include_push_as_half=include_push_as_half)
    X_arr = dataset["X"]
    y_arr = dataset["y"]
    used = dataset["used"]
    pushes = dataset["pushes_seen"]
    dropped = dataset["dropped"]

    if used < min_samples:
        return {
            "status": "insufficient_data",
            "n_samples": used,
            "min_samples": min_samples,
            "pushes_seen": pushes,
            "dropped": dropped,
        }

    clf = LogisticRegression(
        penalty="l2",
        C=1.0,
        max_iter=1000,
        random_state=42,
    )
    clf.fit(X_arr, y_arr)
    probs = clf.predict_proba(X_arr)[:, 1]
    brier = float(np.mean((probs - y_arr) ** 2))
    auc = None
    if len(set(y_arr.tolist())) > 1:
        auc = float(roc_auc_score(y_arr, probs))
    base_rate = float(np.mean(y_arr))

    calibration_id = f"outcome_calib_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now(timezone.utc).isoformat()

    await db.outcome_calibrations.update_many({"is_active": True}, {"$set": {"is_active": False}})
    doc = {
        "outcome_calibration_id": calibration_id,
        "model_type": "LogisticRegression",
        "feature_names": FEATURE_NAMES,
        "features": FEATURE_NAMES,  # backwards compatibility
        "coefficients": [float(v) for v in clf.coef_[0].tolist()],
        "intercept": float(clf.intercept_[0]),
        "created_at": now,
        "n_samples": used,
        "data_cutoff": now[:10],
        "is_active": True,
        "brier_score_train": brier,
        "auc_train": auc,
        "base_rate_covered": base_rate,
        "include_push_as_half": include_push_as_half,
    }
    await db.outcome_calibrations.insert_one(doc)

    return {
        "status": "completed",
        "outcome_calibration_id": calibration_id,
        "n_samples": used,
        "feature_names": FEATURE_NAMES,
        "base_rate_covered": base_rate,
        "brier_score_train": brier,
        "auc_train": auc,
        "coefficients": doc["coefficients"],
        "intercept": doc["intercept"],
        "data_cutoff": doc["data_cutoff"],
        "created_at": now,
    }


async def get_active_outcome_calibration(db) -> Optional[Dict[str, Any]]:
    return await db.outcome_calibrations.find_one({"is_active": True}, {"_id": 0})


async def get_outcome_calibration_diagnostics(db, bins: int = 5) -> Dict[str, Any]:
    calibration = await get_active_outcome_calibration(db)
    if not calibration:
        return {
            "error": "NO_ACTIVE_OUTCOME_CALIBRATION",
            "message": "Run POST /api/admin/model/calibrate-outcome first.",
        }

    picks = await db.predictions.find({"result": {"$in": ["WIN", "LOSS", "PUSH"]}}, {"_id": 0}).to_list(50000)
    include_push_as_half = bool(calibration.get("include_push_as_half", False))
    dataset = _prepare_outcome_dataset(picks, include_push_as_half=include_push_as_half)
    X_arr = dataset["X"]
    y_arr = dataset["y"]
    if len(y_arr) == 0:
        return {
            "error": "NO_DIAGNOSTIC_SAMPLES",
            "message": "No WIN/LOSS samples with complete features available.",
            "n_samples": 0,
        }

    probs = []
    model_edges = []
    for row in X_arr:
        p = predict_p_cover_outcome(
            model_edge=float(row[0]),
            open_price=float(row[1]),
            open_spread=float(row[2]),
            calibration_doc=calibration,
        )
        if p is None:
            continue
        probs.append(float(p))
        model_edges.append(float(row[0]))

    if not probs:
        return {
            "error": "FAILED_PROBABILITY_INFERENCE",
            "message": "Could not compute p_hat from active outcome calibration.",
            "n_samples": 0,
        }

    n = min(len(probs), len(y_arr))
    probs = probs[:n]
    labels = y_arr[:n]

    brier = float(np.mean((np.array(probs) - labels) ** 2))
    auc = None
    if len(set(labels.tolist())) > 1:
        auc = float(roc_auc_score(labels, probs))

    bins = max(2, min(int(bins), 20))
    edges = np.linspace(0.0, 1.0, bins + 1)
    bin_rows = []
    for i in range(bins):
        lo = float(edges[i])
        hi = float(edges[i + 1])
        if i == bins - 1:
            idx = [j for j, p in enumerate(probs) if lo <= p <= hi]
        else:
            idx = [j for j, p in enumerate(probs) if lo <= p < hi]
        if not idx:
            bin_rows.append(
                {
                    "bin": f"[{lo:.2f},{hi:.2f}{']' if i == bins - 1 else ')'}",
                    "n": 0,
                    "mean_p_hat": None,
                    "winrate": None,
                }
            )
            continue
        p_mean = float(np.mean([probs[j] for j in idx]))
        winrate = float(np.mean([labels[j] for j in idx]))
        bin_rows.append(
            {
                "bin": f"[{lo:.2f},{hi:.2f}{']' if i == bins - 1 else ')'}",
                "n": len(idx),
                "mean_p_hat": p_mean,
                "winrate": winrate,
            }
        )

    edge_ranges = [
        ("<1.5", lambda x: abs(x) < 1.5),
        ("1.5-2.5", lambda x: 1.5 <= abs(x) < 2.5),
        ("2.5-3.5", lambda x: 2.5 <= abs(x) < 3.5),
        (">=3.5", lambda x: abs(x) >= 3.5),
    ]
    edge_summary = []
    for label, pred in edge_ranges:
        idx = [i for i, edge in enumerate(model_edges[:n]) if pred(edge)]
        if not idx:
            edge_summary.append({"edge_bucket": label, "n": 0, "winrate": None, "mean_p_hat": None})
            continue
        edge_summary.append(
            {
                "edge_bucket": label,
                "n": len(idx),
                "winrate": float(np.mean([labels[i] for i in idx])),
                "mean_p_hat": float(np.mean([probs[i] for i in idx])),
            }
        )

    coef = calibration.get("coefficients") or []
    feature_names = calibration.get("feature_names") or FEATURE_NAMES
    coef_by_feature = []
    for i, name in enumerate(feature_names):
        coef_by_feature.append({"feature": name, "coefficient": float(coef[i]) if i < len(coef) else None})

    return {
        "n_samples": int(n),
        "base_rate_covered": float(np.mean(labels)),
        "brier_score": brier,
        "auc": auc,
        "feature_names": feature_names,
        "coefficients": coef,
        "coef_by_feature": coef_by_feature,
        "intercept": calibration.get("intercept"),
        "data_cutoff": calibration.get("data_cutoff"),
        "bins": bin_rows,
        "model_edge_summary": edge_summary,
        "pushes_seen": dataset["pushes_seen"],
        "dropped_missing_features": dataset["dropped"],
        "include_push_as_half": include_push_as_half,
    }
