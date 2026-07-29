# Haven v0.4 Hermes Integration — Install Instructions

## Prerequisites
- Hermes Agent installed at ~/.hermes/hermes-agent/
- Python 3.11+ with pandas, numpy, matplotlib, pyyaml in Hermes venv

## Full Installation (plugin + skill + cron wrapper script)
```bash
cd /Users/wth/haven_research_v0_4/haven_research

/Users/wth/.hermes/hermes-agent/venv/bin/python3 integrations/hermes/install.py
```

This copies:
- Plugin → ~/.hermes/plugins/haven_market_model/
- Skill → ~/.hermes/skills/haven-market-risk/SKILL.md
- Close-update script → ~/.hermes/scripts/haven-close-update.py
- Enables plugin in config.yaml

## Verification
```bash
# 1. Skill loads on macOS (no longer Linux-only)
hermes skill view haven-market-risk

# 2. Plugin tools registered
hermes tools | grep haven

# 3. Smoke-test each tool
VENV=/Users/wth/.hermes/hermes-agent/venv/bin/python3
$VENV -c "
import sys, json
sys.path.insert(0, 'integrations/hermes')
from haven_market_model.tools import handle_model_status, handle_backtest_v04
print(json.dumps(json.loads(handle_model_status({'refresh':False})), indent=2))
"

# 4. Run 24 tests
$VENV -m unittest discover -s tests -v
```

## Configuration
- Plugin YAML: ~/.hermes/plugins/haven_market_model/plugin.yaml
- Skill: ~/.hermes/skills/haven-market-risk/SKILL.md
- Engine path: ~/.hermes/plugins/haven_market_model/engine_path.json
- Snapshot: outputs/ten_year_v0_4_enriched/live/current_snapshot.json

## Cron (NOT ENABLED — requires Codex PASS approval)
```bash
# After approval:
python3 integrations/hermes/install.py --update --create-cron --deliver telegram
```
