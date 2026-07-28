# Haven v0.4 Hermes Integration - Install Instructions

## Prerequisites
- Hermes Agent installed at ~/.hermes/hermes-agent/
- Python 3.11+ with pandas, numpy, matplotlib, pyyaml in Hermes venv

## Installation
```bash
cd /Users/wth/haven_research_v0_4/haven_research

# Install plugin + skill + command (no cron)
/Users/wth/.hermes/hermes-agent/venv/bin/python3 integrations/hermes/install.py

# Verify plugin enabled in config.yaml:
#   plugins:
#     enabled:
#       - haven-market-model
```

## Verification
```bash
# Test all plugin tools
cd /Users/wth/haven_research_v0_4/haven_research
VENV=/Users/wth/.hermes/hermes-agent/venv/bin/python3
$VENV -c "
import sys, json
sys.path.insert(0, 'integrations/hermes')
from haven_market_model.tools import handle_model_status, handle_backtest_v04
print(json.dumps(json.loads(handle_model_status({'refresh':False})), indent=2))
"

# Run 24 tests
$VENV -m unittest discover -s tests -v
```

## Configuration
- Plugin YAML: ~/.hermes/plugins/haven_market_model/plugin.yaml
- Skill: ~/.hermes/skills/haven-market-risk/SKILL.md (also plugin-registered as haven-market-model:haven-market-risk)
- Engine path: ~/.hermes/plugins/haven_market_model/engine_path.json
- Snapshot: outputs/ten_year_v0_4_enriched/live/current_snapshot.json
