"""分開跑 strategy 2-5（避免 tee buffer 問題）"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Import the functions
import importlib.util
spec = importlib.util.spec_from_file_location("rsb", ROOT / "scripts" / "reverse_signals_batch.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("執行 strategy 2-5...\n")
mod.strat_consecutive_limitup_cooldown()
mod.strat_monthly_revenue()
mod.strat_margin_surge()
mod.strat_vix_spike()
print("\n全完成。")
