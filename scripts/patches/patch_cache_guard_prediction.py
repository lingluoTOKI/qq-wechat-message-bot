"""Local patch for astrbot_plugin_cache_guard 1.2.0.

Problem: the prediction/calibration path is gated by `guard_on`
(trim_mode == "observed_guard"). With trim_mode=off (cache-first setups)
the plugin never predicts anything and always logs "predicted_input=uncalibrated",
so the calibrated token estimate is useless even as a diagnostic.

Fix: compute the prediction whenever the calibration is usable
(same system/tools/model signature + a real usage sample), independently of
trim_mode. The actual history trimming stays behind `guard_on`, so
trim_mode=off keeps behaving exactly as before (no trimming).

Also adds prediction error tracking (predicted vs real DeepSeek input tokens)
so the estimate can be trusted / audited.
"""

import io
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "main.py"
s = io.open(path, encoding="utf-8").read()
if "prediction_min_samples" in s:
    print("already patched")
    raise SystemExit

# --- R1: new stats fields -------------------------------------------------
old = "    successful_calibration_samples: int = 0\n"
new = (
    "    successful_calibration_samples: int = 0\n"
    "    prediction_samples: int = 0\n"
    "    prediction_error_sum: float = 0.0\n"
    "    last_prediction_error: float | None = None\n"
    "    last_prediction_pair: tuple | None = None\n"
)
assert old in s, "R1 anchor not found"
s = s.replace(old, new, 1)

# --- R2: decouple prediction from trim_mode ------------------------------
old = """        if (guard_on and st.successful_calibration_samples >=
                self._cfg_int("trim_warmup_samples", 5, 1, 30)
                and st.last_actual_input is not None
                and st.last_estimated_content is not None
                and st.last_signature == signature and not st.pending):
            predicted = max(0, st.last_actual_input + before_est - st.last_estimated_content)
            st.last_predicted_input = predicted
            hard = self._cfg_int("hard_input_limit_tokens", 18000, 4000, 200000)
            target = self._cfg_int("trim_target_tokens", 12500, 2000, 190000)
            target = min(target, hard - 1500)
            if predicted > hard and st.trim_cooldown_left == 0:
"""
new = """        min_pred_samples = self._cfg_int("prediction_min_samples", 2, 1, 30)
        can_predict = (
            st.successful_calibration_samples >= min_pred_samples
            and st.last_actual_input is not None
            and st.last_estimated_content is not None
            and st.last_signature == signature and not st.pending
        )
        if can_predict:
            predicted = max(0, st.last_actual_input + before_est - st.last_estimated_content)
            st.last_predicted_input = predicted
        if (guard_on and st.successful_calibration_samples >=
                self._cfg_int("trim_warmup_samples", 5, 1, 30)
                and predicted is not None and st.trim_cooldown_left == 0):
            hard = self._cfg_int("hard_input_limit_tokens", 18000, 4000, 200000)
            target = self._cfg_int("trim_target_tokens", 12500, 2000, 190000)
            target = min(target, hard - 1500)
            if predicted > hard:
"""
assert old in s, "R2 anchor not found"
s = s.replace(old, new, 1)

old = """        elif guard_on:
            st.guard_skips_no_usage += 1
"""
new = """        elif guard_on and not can_predict:
            st.guard_skips_no_usage += 1
"""
assert old in s, "R2b anchor not found"
s = s.replace(old, new, 1)

# --- R3: carry the prediction into the pending queue ----------------------
old = "        st.pending.append((after_est, signature))\n"
new = "        st.pending.append((after_est, signature, predicted))\n"
assert old in s, "R3 anchor not found"
s = s.replace(old, new, 1)

# --- R4: measure prediction error against the real usage ------------------
old = """            if pending is not None:
                st.last_estimated_content, st.last_signature = pending
                st.last_actual_input = hit + miss
"""
new = """            if pending is not None:
                st.last_estimated_content, st.last_signature = pending[0], pending[1]
                st.last_actual_input = hit + miss
                if len(pending) > 2 and pending[2] is not None and (hit + miss) > 0:
                    actual = hit + miss
                    err = abs(pending[2] - actual) * 100.0 / actual
                    st.prediction_samples += 1
                    st.prediction_error_sum += err
                    st.last_prediction_error = err
                    st.last_prediction_pair = (pending[2], actual)
"""
assert old in s, "R4 anchor not found"
s = s.replace(old, new, 1)

# --- R5: log the prediction error ----------------------------------------
import re as _re

_m = _re.search(
    r'"\[CacheGuard 1\.(\d)\] cache session=%s input=%s cached=%s miss=%s rate=%s",', s
)
assert _m, "R5 anchor not found"
_ver = _m.group(1)
old = _m.group(0)
new = (
    f'"[CacheGuard 1.{_ver}] cache session=%s input=%s cached=%s miss=%s rate=%s "\n'
    '                "predicted=%s pred_err=%s",'
)
s = s.replace(old, new, 1)
old = """                cached, other, st.hit_rate,
            )
"""
new = """                cached, other, st.hit_rate,
                (st.last_prediction_pair[0] if st.last_prediction_pair else "n/a"),
                (f"{st.last_prediction_error:.1f}%" if st.last_prediction_error is not None else "n/a"),
            )
"""
assert old in s, "R5b anchor not found"
s = s.replace(old, new, 1)

# --- R6: show calibration quality in /缓存状态 ----------------------------
old = """            f"上轮实际输入：{st.last_actual_input if st.last_actual_input is not None else '未校准'} Token；"
            f"本轮预测：{st.last_predicted_input if st.last_predicted_input is not None else '未校准'} Token\\n"
"""
new = """            f"上轮实际输入：{st.last_actual_input if st.last_actual_input is not None else '未校准'} Token；"
            f"本轮预测：{st.last_predicted_input if st.last_predicted_input is not None else '未校准'} Token\\n"
            f"预测校准：样本 {st.prediction_samples} 次，"
            f"平均误差 {(st.prediction_error_sum / st.prediction_samples if st.prediction_samples else 0):.1f}%，"
            f"上一轮 预测 {st.last_prediction_pair[0] if st.last_prediction_pair else 'n/a'} / "
            f"实际 {st.last_prediction_pair[1] if st.last_prediction_pair else 'n/a'}\\n"
"""
assert old in s, "R6 anchor not found"
s = s.replace(old, new, 1)

# --- R7: aggregate counters in /缓存总体 ----------------------------------
old = """                "stable_growth_events", "broken_history_events", "max_uncached_plateau", "changed_model",
"""
new = """                "stable_growth_events", "broken_history_events", "max_uncached_plateau", "changed_model",
                "prediction_samples", "prediction_error_sum",
"""
assert old in s, "R7 anchor not found"
s = s.replace(old, new, 1)

io.open(path, "w", encoding="utf-8").write(s)
print("cache_guard patched:", path)

# --- schema: new option ---------------------------------------------------
schema_path = path.rsplit("/", 1)[0] + "/_conf_schema.json"
try:
    sc = json.load(io.open(schema_path, encoding="utf-8-sig"))
    if "prediction_min_samples" not in sc:
        sc["prediction_min_samples"] = {
            "description": "开始输出 token 预测所需的最小成功校准样本数",
            "type": "int",
            "default": 2,
            "hint": "与裁剪无关：仅决定“本轮预测 Token”何时开始显示（需同签名 + 已有真实用量样本）。",
        }
        io.open(schema_path, "w", encoding="utf-8").write(
            json.dumps(sc, ensure_ascii=False, indent=2)
        )
        print("schema patched:", schema_path)
except Exception as exc:  # noqa: BLE001
    print("schema skip:", exc)
